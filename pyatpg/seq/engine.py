"""Sequential ATPG orchestrator.

Combines candidate sources (PODEM, random-fill, mutation, and optionally an
injected LLM-2 prioritizer) with the deterministic simulator as the sole
arbiter of what actually got detected. No network/LLM code lives in this
module -- ``candidate_ranker`` is a plain callable injected from
:mod:`pyatpg.llm`, keeping prompts and the ATPG engine independent of each
other (the engine works, and is fully tested, with no LLM at all). The one
thing it knows about the LLM layer is :class:`LLMUnavailableError`, which
it must let through: a ranker that *could not run* is not the same as a
ranker that gave an unhelpful answer, and only the latter is degraded around.

Optimisation objective is explicitly *not* "maximum coverage": the loop
stops as soon as ``target_coverage`` is reached, or the budgets run out,
whichever first -- so the reported test set is the smallest one the engine
found that hits the target, not the largest one it could produce.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from pyatpg.core.netlist import Circuit
from pyatpg.ranking.providers import LLMUnavailableError

from .faultsim import checkpoint_fault_list, evaluate_coverage
from .generator import mutate, random_sequence
from .models import SequentialAnalysis, SequentialFault, SequentialResults, TestSequence
from .podem import podem
from .timeframe import build_unrolled_circuit

#: A candidate_ranker takes the current undetected faults plus a small
#: context dict and returns a reordered fault list (hardest/most-worthwhile
#: first) or None to fall back to natural order. See pyatpg.llm.vector_generator.
CandidateRanker = Callable[[list[SequentialFault], dict], "list[SequentialFault] | None"]

#: A delta callback is invoked once per accepted sequence with a small dict
#: (coverage before/after, newly detected fault names, sequence length) --
#: this is what a caller wires up to stream progress without keeping the
#: full simulation history around.
DeltaCallback = Callable[[dict], None]


@dataclass
class EngineConfig:
    target_coverage: float = 100.0
    max_sequences: int = 60
    base_cycles: int = 4
    max_cycles: int = 10
    random_pool_size: int = 40
    podem_max_frames: int = 6
    podem_max_backtracks: int = 150
    #: faults left undetected after the no-LLM phases before the ranker is
    #: consulted at all -- keeps LLM involvement to genuinely hard faults.
    llm_activation_threshold: int = 1
    rng_seed: int | None = 1234


@dataclass
class _RunState:
    faults: list[SequentialFault]
    sequences: list[TestSequence] = field(default_factory=list)
    results: SequentialResults | None = None
    undetected: list[SequentialFault] = field(default_factory=list)


class SequentialAtpgEngine:
    def __init__(
        self,
        circuit: Circuit,
        circuit_name: str = "circuit",
        config: EngineConfig | None = None,
        candidate_ranker: CandidateRanker | None = None,
        on_delta: DeltaCallback | None = None,
    ):
        self.circuit = circuit
        self.circuit_name = circuit_name
        self.config = config or EngineConfig()
        self.candidate_ranker = candidate_ranker
        self.on_delta = on_delta
        self.rng = random.Random(self.config.rng_seed)

    # ------------------------------------------------------------ helpers
    def _score(self, state: _RunState, candidate: TestSequence) -> tuple[bool, list[str]]:
        """Try one candidate against only the *currently undetected* faults.

        Detection is monotonic here: every sequence is simulated
        independently from a fixed reset state (see ``simulator.simulate``),
        so a fault a prior sequence already detected cannot become
        undetected by adding another one. That makes it safe -- and, for
        anything past a trivial circuit, essential for performance -- to
        never re-check faults a previous sequence already covered instead of
        re-running the whole fault list against the whole growing sequence
        set on every trial.
        """
        if state.results is None:
            state.results = SequentialResults(
                total_faults=len(state.faults),
                detected_faults=0,
                undetected_faults=len(state.faults),
                aborted_faults=0,
                fault_coverage=0.0,
                sequence_count=0,
                total_clock_cycles=0,
                max_sequence_length=0,
            )
        if not state.undetected:
            return False, []

        _partial_results, _detections, still_undetected, delta = evaluate_coverage(
            self.circuit, state.undetected, [candidate]
        )
        newly = delta[0] if delta else []
        accepted = bool(newly)
        if accepted:
            before = state.results.fault_coverage
            state.sequences.append(candidate)
            state.undetected = still_undetected
            total = state.results.total_faults or len(state.faults)
            detected_so_far = total - len(state.undetected)
            state.results = SequentialResults(
                total_faults=total,
                detected_faults=detected_so_far,
                undetected_faults=len(state.undetected),
                aborted_faults=0,
                fault_coverage=round(100.0 * detected_so_far / total, 3) if total else 0.0,
                sequence_count=len(state.sequences),
                total_clock_cycles=sum(s.cycles for s in state.sequences),
                max_sequence_length=max((s.cycles for s in state.sequences), default=0),
            )
            if self.on_delta:
                self.on_delta(
                    {
                        "coverage_before": before,
                        "coverage_after": state.results.fault_coverage,
                        "newly_detected": newly,
                        "sequence_length": candidate.cycles,
                        "sequence_origin": candidate.origin,
                        "sequence_count": len(state.sequences),
                    }
                )
        return accepted, newly

    def _at_target(self, state: _RunState) -> bool:
        return bool(
            state.results and state.results.fault_coverage is not None
            and state.results.fault_coverage >= self.config.target_coverage
        )

    def _budget_left(self, state: _RunState) -> bool:
        return len(state.sequences) < self.config.max_sequences

    def _podem_candidate(self, fault: SequentialFault) -> TestSequence | None:
        for n_frames in range(1, self.config.podem_max_frames + 1):
            unrolled = build_unrolled_circuit(self.circuit, n_frames)
            fault_node = f"f0_{fault.node}"
            po_names = set(unrolled.po_frames.keys())
            result = podem(
                unrolled.circuit,
                fault_node,
                fault.stuck_at,
                po_names,
                max_backtracks=self.config.podem_max_backtracks,
            )
            if result.success:
                vectors: list[dict[str, int]] = [dict() for _ in range(n_frames)]
                for uname, (frame, orig) in unrolled.pi_frames.items():
                    vectors[frame][orig] = result.pi_values.get(uname, self.rng.randint(0, 1))
                return TestSequence(vectors=vectors, origin=f"podem[{n_frames}f]")
        return None

    # ------------------------------------------------------------ phases
    def _phase_random_fill(self, state: _RunState) -> None:
        for _ in range(self.config.random_pool_size):
            if not self._budget_left(state) or self._at_target(state):
                return
            cand = random_sequence(self.circuit, self.config.base_cycles, self.rng)
            self._score(state, cand)

    def _phase_podem(self, state: _RunState) -> None:
        for fault in list(state.undetected):
            if not self._budget_left(state) or self._at_target(state):
                return
            if fault not in state.undetected:
                continue  # already covered incidentally by an earlier acceptance
            candidate = self._podem_candidate(fault)
            if candidate is None:
                continue
            accepted, _ = self._score(state, candidate)
            if not accepted:
                for _ in range(2):
                    if not self._budget_left(state):
                        break
                    mutated = mutate(candidate, self.rng)
                    accepted, _ = self._score(state, mutated)
                    if accepted:
                        break

    def _phase_llm(self, state: _RunState) -> list[str]:
        """LLM-2-assisted attempts on faults that survived the deterministic phases.

        Only runs when a ranker is injected and enough hard faults remain to
        justify the call -- see ``llm_activation_threshold``.
        """
        notes: list[str] = []
        if self.candidate_ranker is None:
            return notes
        if len(state.undetected) < self.config.llm_activation_threshold:
            return notes
        if self._at_target(state) or not self._budget_left(state):
            return notes

        try:
            ranked = self.candidate_ranker(list(state.undetected), {"circuit_name": self.circuit_name})
        except LLMUnavailableError:
            # The provider itself failed (credits, rate limit, outage). The
            # user asked for an LLM-assisted run, so stop and report that
            # instead of quietly finishing without the model.
            raise
        except Exception as exc:  # noqa: BLE001 - ranker ran but its answer was unusable
            notes.append(f"LLM candidate ranking unavailable ({exc}); used natural fault order.")
            ranked = None

        ordered = ranked or list(state.undetected)
        for fault in ordered:
            if not self._budget_left(state) or self._at_target(state):
                break
            if fault not in state.undetected:
                continue
            candidate = self._podem_candidate(fault)
            if candidate is not None:
                accepted, _ = self._score(state, candidate)
                if accepted:
                    continue
            # deeper local search as a last resort for this fault
            for _ in range(3):
                if not self._budget_left(state):
                    break
                base = candidate or random_sequence(self.circuit, self.config.max_cycles, self.rng)
                mutated = mutate(base, self.rng, flip_rate=0.25)
                accepted, _ = self._score(state, mutated)
                if accepted:
                    break
        return notes

    # -------------------------------------------------------------- run
    def run(self) -> SequentialAnalysis:
        faults = checkpoint_fault_list(self.circuit)
        state = _RunState(faults=faults, undetected=list(faults))
        notes: list[str] = []

        self._phase_random_fill(state)
        if not self._at_target(state) and self._budget_left(state):
            self._phase_podem(state)
        if not self._at_target(state) and self._budget_left(state):
            notes.extend(self._phase_llm(state))

        results = state.results or SequentialResults(
            total_faults=len(faults),
            detected_faults=0,
            undetected_faults=len(faults),
            aborted_faults=0,
            fault_coverage=0.0,
            sequence_count=0,
            total_clock_cycles=0,
            max_sequence_length=0,
        )
        _final_results, _detections, undetected, newly_by_seq = evaluate_coverage(
            self.circuit, faults, state.sequences
        )
        detected_names = sorted(set(f.name for f in faults) - set(f.name for f in undetected))

        return SequentialAnalysis(
            circuit_name=self.circuit_name,
            results=_final_results,
            sequences=state.sequences,
            detected=detected_names,
            undetected=[f.name for f in undetected],
            newly_detected_by_sequence=newly_by_seq,
            notes=notes,
        )
