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
