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
import time
from dataclasses import dataclass, field
from typing import Callable

from pyatpg.core.netlist import Circuit
from pyatpg.core.progress import report
from pyatpg.ranking.providers import LLMUnavailableError

from .faultsim import checkpoint_fault_list, evaluate_coverage
from .generator import mutate, random_sequence
from .models import SequentialAnalysis, SequentialFault, SequentialResults, TestSequence
from .podem import _topo_sort_gates, podem
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


#: What the progress display says during each search phase.
_PHASE_LABELS = {
    "random_fill": "Sequential ATPG: random test sequences",
    "podem": "Sequential ATPG: PODEM search across clock cycles",
    "llm_guided": "Sequential ATPG: LLM-guided deep search",
    "extended_search": "Sequential ATPG: extended deep search",
}


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
    #: Wall-clock seconds one run may spend searching before it stops and
    #: reports the coverage reached so far (None = unlimited). PODEM on a
    #: large unrolled circuit can otherwise run for many minutes -- past the
    #: web UI's request timeout, which then shows nothing at all. The
    #: deterministic phases get ``deterministic_share`` of it, so an
    #: LLM-assisted run still has time left for its own phase.
    #: Was 100 s; incremental PODEM implication (pyatpg.seq.podem) made the
    #: search ~18x faster per step, so 40 s now searches several times
    #: further than 100 s used to (s5378: 69.3% coverage in 24 s, vs 69.7% in
    #: 104 s before), and a comparison's two runs finish far sooner.
    time_budget_s: float | None = 40.0
    deterministic_share: float = 0.6
    #: Run the deep-search phase even without a ranker, in natural fault
    #: order. The deterministic-only side of a comparison sets this, so both
    #: sides get the same search effort and any coverage gap is attributable
    #: to the LLM's *ordering* rather than to the LLM run simply searching
    #: longer.
    extended_search: bool = False

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


#: Why a fault ended the run undetected -- the sequential counterpart of
#: Atalanta's redundant / aborted classification.
OUTCOME_EXHAUSTED = "podem_exhausted"
OUTCOME_ABORTED = "aborted"
OUTCOME_NOT_CONFIRMED = "test_not_confirmed"
OUTCOME_NOT_ATTEMPTED = "not_attempted"
OUTCOME_DESCRIPTIONS = {
    OUTCOME_EXHAUSTED: "PODEM searched every unrolling depth to exhaustion without finding a "
    "test. Not a proof of redundancy: the search injects the fault in the first frame only and "
    "treats the starting state as free.",
    OUTCOME_ABORTED: "PODEM hit its backtrack limit before finding a test or exhausting the search.",
    OUTCOME_NOT_CONFIRMED: "PODEM found a test in the unrolled circuit, but simulated from reset it "
    "did not detect the fault (the state it assumed is unreachable, or filled inputs differed).",
    OUTCOME_NOT_ATTEMPTED: "The sequence/time budget ran out before this fault was searched.",
}


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
        #: fault name -> OUTCOME_* for faults PODEM was run on
        self._outcomes: dict[str, str] = {}
        self._phase = "random_fill"
        self._phase_stats: dict[str, dict] = {}

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
        stats = self._phase_stats.setdefault(
            self._phase, {"candidates": 0, "accepted": 0, "faults_detected": 0, "seconds": 0.0}
        )
        stats["candidates"] += 1
        if accepted:
            stats["accepted"] += 1
            stats["faults_detected"] += len(newly)
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
        if len(state.sequences) >= self.config.max_sequences:
            return False
        deadline = getattr(self, "_deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            self._timed_out = True
            return False
        return True

    def _unrolled(self, n_frames: int):
        """The ``n_frames``-deep unrolled circuit, its PO set and gate order.

        Built once per depth and reused for every fault: the unrolled netlist
        depends only on the circuit and the depth, never on the fault.
        """
        cache = self.__dict__.setdefault("_unrolled_cache", {})
        if n_frames not in cache:
            unrolled = build_unrolled_circuit(self.circuit, n_frames)
            cache[n_frames] = (
                unrolled,
                set(unrolled.po_frames.keys()),
                _topo_sort_gates(unrolled.circuit),
            )
        return cache[n_frames]

    def _podem_candidate(self, fault: SequentialFault) -> TestSequence | None:
        any_aborted = False
        for n_frames in range(1, self.config.podem_max_frames + 1):
            unrolled, po_names, topo_order = self._unrolled(n_frames)
            fault_node = f"f0_{fault.node}"
            result = podem(
                unrolled.circuit,
                fault_node,
                fault.stuck_at,
                po_names,
                max_backtracks=self.config.podem_max_backtracks,
                topo_order=topo_order,
            )
            if result.success:
                vectors: list[dict[str, int]] = [dict() for _ in range(n_frames)]
                for uname, (frame, orig) in unrolled.pi_frames.items():
                    vectors[frame][orig] = result.pi_values.get(uname, self.rng.randint(0, 1))
                self._outcomes[fault.name] = OUTCOME_NOT_CONFIRMED
                return TestSequence(vectors=vectors, origin=f"podem[{n_frames}f]")
            any_aborted = any_aborted or result.aborted
        self._outcomes[fault.name] = OUTCOME_ABORTED if any_aborted else OUTCOME_EXHAUSTED
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
        if self.candidate_ranker is None and not self.config.extended_search:
            return notes
        if len(state.undetected) < self.config.llm_activation_threshold:
            return notes
        if self._at_target(state) or not self._budget_left(state):
            return notes

        if self.candidate_ranker is None:
            ranked = None  # extended search: same effort, natural fault order
        else:
            try:
                ranked = self.candidate_ranker(
                    list(state.undetected), {"circuit_name": self.circuit_name}
                )
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
    def _run_phase(self, name: str, fn, state: _RunState):
        self._phase = name
        report(_PHASE_LABELS.get(name, f"Sequential ATPG: {name.replace('_', ' ')}"))
        stats = self._phase_stats.setdefault(
            name, {"candidates": 0, "accepted": 0, "faults_detected": 0, "seconds": 0.0}
        )
        t0 = time.monotonic()
        try:
            return fn(state)
        finally:
            stats["seconds"] = round(stats["seconds"] + time.monotonic() - t0, 3)

    def _node_kind(self, node: str) -> str:
        gate = self.circuit.gates.get(node)
        if gate is not None:
            return gate.gtype
        if node in {ff.q for ff in self.circuit.flipflops}:
            return "FF-Q"
        return "PI"

    def run(self) -> SequentialAnalysis:
        faults = checkpoint_fault_list(self.circuit)
        state = _RunState(faults=faults, undetected=list(faults))
        notes: list[str] = []
        budget = self.config.time_budget_s
        start = time.monotonic()
        self._timed_out = False

        self._deadline = start + budget * self.config.deterministic_share if budget else None
        self._run_phase("random_fill", self._phase_random_fill, state)
        if not self._at_target(state) and self._budget_left(state):
            self._run_phase("podem", self._phase_podem, state)
        self._deadline = start + budget if budget else None
        extra_phase = "llm_guided" if self.candidate_ranker is not None else "extended_search"
        if not self._at_target(state) and self._budget_left(state):
            if self.candidate_ranker is not None or self.config.extended_search:
                notes.extend(self._run_phase(extra_phase, self._phase_llm, state))
        search_seconds = time.monotonic() - start
        stopped_by = "target_coverage" if self._at_target(state) else (
            "time_budget" if self._timed_out else (
                "max_sequences" if len(state.sequences) >= self.config.max_sequences
                else "search_exhausted"
            )
        )
        if self._timed_out:
            notes.append(
                f"Stopped at the {budget:g}s search time budget; the coverage shown is "
                f"what was reached by then, not the engine's final result."
            )
        self._deadline = None

        t0 = time.monotonic()
        final, _detections, undetected, newly_by_seq = evaluate_coverage(
            self.circuit, faults, state.sequences
        )
        final_sim_seconds = time.monotonic() - t0
        detected_names = sorted(set(f.name for f in faults) - set(f.name for f in undetected))

        fault_details = []
        counts = {k: 0 for k in OUTCOME_DESCRIPTIONS}
        for f in undetected:
            outcome = self._outcomes.get(f.name, OUTCOME_NOT_ATTEMPTED)
            counts[outcome] += 1
            fault_details.append({
                "fault": f.name,
                "node": f.node,
                "node_type": self._node_kind(f.node),
                "stuck_at": f.stuck_at,
                "status": outcome,
            })
        final.aborted_faults = counts[OUTCOME_ABORTED]
        final.podem_exhausted_faults = counts[OUTCOME_EXHAUSTED]
        final.not_confirmed_faults = counts[OUTCOME_NOT_CONFIRMED]
        final.not_attempted_faults = counts[OUTCOME_NOT_ATTEMPTED]

        return SequentialAnalysis(
            circuit_name=self.circuit_name,
            results=final,
            sequences=state.sequences,
            detected=detected_names,
            undetected=[f.name for f in undetected],
            newly_detected_by_sequence=newly_by_seq,
            notes=notes,
            fault_details=fault_details,
            outcome_descriptions=dict(OUTCOME_DESCRIPTIONS),
            phases=[{"phase": k, **v} for k, v in self._phase_stats.items()],
            parameters=self.config.to_dict(),
            timing={
                "search_s": round(search_seconds, 3),
                "final_fault_sim_s": round(final_sim_seconds, 3),
                "total_s": round(search_seconds + final_sim_seconds, 3),
            },
            stopped_by=stopped_by,
        )
