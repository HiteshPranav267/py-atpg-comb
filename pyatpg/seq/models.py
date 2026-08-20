"""Data model for the sequential ATPG engine.

Mirrors the shape of ``pyatpg.atpg.models`` (Optional fields, dataclasses,
no fabricated numbers) so the API layer can expose combinational and
sequential results side by side without the caller needing two mental
models.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SequentialFault:
    """A single stuck-at fault on a net of the *original* (rolled-up) circuit.

    ``node`` is the gate output, primary input, or flip-flop Q the fault
    sits on -- never a frame-qualified name; frame-qualification only exists
    inside the unrolled netlist used for search.
    """

    node: str
    stuck_at: int  # 0 or 1

    @property
    def name(self) -> str:
        return f"{self.node}/{self.stuck_at}"

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return self.name


@dataclass
class TestSequence:
    """A multi-cycle input sequence: one PI->value vector per clock cycle."""

    __test__ = False  # not a pytest test case despite the name

    vectors: list[dict[str, int]] = field(default_factory=list)
    #: Where this sequence came from, for reporting / debugging only.
    origin: str = "unknown"

    @property
    def cycles(self) -> int:
        return len(self.vectors)

    def to_dict(self) -> dict[str, Any]:
        return {"vectors": self.vectors, "cycles": self.cycles, "origin": self.origin}


@dataclass
class SequentialResults:
    """Coverage and effort metrics, computed only by deterministic simulation."""

    total_faults: int | None = None
    detected_faults: int | None = None
    undetected_faults: int | None = None
    aborted_faults: int | None = None
    fault_coverage: float | None = None
    sequence_count: int | None = None
    total_clock_cycles: int | None = None
    max_sequence_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SequentialAnalysis:
    """The complete result of a sequential ATPG run.

    Structured to sit alongside :class:`pyatpg.atpg.models.AtalantaRun` in an
    API response without reusing its fields, since a sequential run has no
    single-cycle notion of "a test vector".
    """

    circuit_name: str
    results: SequentialResults = field(default_factory=SequentialResults)
    sequences: list[TestSequence] = field(default_factory=list)
    detected: list[str] = field(default_factory=list)
    undetected: list[str] = field(default_factory=list)
    newly_detected_by_sequence: list[list[str]] = field(default_factory=list)
    llm: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "circuit_name": self.circuit_name,
            "results": self.results.to_dict(),
            "sequences": [s.to_dict() for s in self.sequences],
            "detected": self.detected,
            "undetected": self.undetected,
            "newly_detected_by_sequence": self.newly_detected_by_sequence,
            "llm": self.llm,
            "notes": self.notes,
        }


@dataclass
class SequentialComparison:
    """Deterministic-only vs LLM-assisted, run back to back on the same
    circuit with the same random seed so the only thing that can differ is
    what LLM-2 (and the deeper local search it unlocks) contributed.

    Mirrors ``pyatpg.atpg.models.RunComparison`` in spirit: both full
    results are kept so nothing is lost, plus a small delta summary so the
    caller doesn't have to recompute it.
    """

    baseline: SequentialAnalysis
    llm_assisted: SequentialAnalysis
    coverage_delta: float | None = None
    additional_faults_detected: list[str] = field(default_factory=list)
    additional_sequences: int | None = None
    additional_clock_cycles: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "llm_assisted": self.llm_assisted.to_dict(),
            "coverage_delta": self.coverage_delta,
            "additional_faults_detected": self.additional_faults_detected,
            "additional_sequences": self.additional_sequences,
            "additional_clock_cycles": self.additional_clock_cycles,
            "notes": self.notes,
        }


def build_sequential_comparison(
    baseline: SequentialAnalysis, llm_assisted: SequentialAnalysis
) -> SequentialComparison:
    """Compare a deterministic-only run against an LLM-assisted one.

    Both are full, independently-simulated results -- this never invents a
    delta from anything but two real ``SequentialAnalysis`` results.
    """
    b_cov = baseline.results.fault_coverage
    l_cov = llm_assisted.results.fault_coverage
    coverage_delta = round(l_cov - b_cov, 3) if b_cov is not None and l_cov is not None else None

    additional = sorted(set(llm_assisted.detected) - set(baseline.detected))

    b_seq = baseline.results.sequence_count
    l_seq = llm_assisted.results.sequence_count
    additional_sequences = (l_seq - b_seq) if b_seq is not None and l_seq is not None else None

    b_cycles = baseline.results.total_clock_cycles
    l_cycles = llm_assisted.results.total_clock_cycles
    additional_cycles = (
        (l_cycles - b_cycles) if b_cycles is not None and l_cycles is not None else None
    )

    notes = [
        "Both runs use the same random seed, so random-fill and PODEM search "
        "identical candidates first; only the LLM-2-assisted phase (and the "
        "deeper local search it unlocks) can differ between the two columns."
    ]
    if coverage_delta is not None and coverage_delta <= 0 and not additional:
        notes.append(
            "LLM assistance made no measurable difference on this circuit: "
            "deterministic search alone already found everything it could."
        )

    return SequentialComparison(
        baseline=baseline,
        llm_assisted=llm_assisted,
        coverage_delta=coverage_delta,
        additional_faults_detected=additional,
        additional_sequences=additional_sequences,
        additional_clock_cycles=additional_cycles,
        notes=notes,
    )
