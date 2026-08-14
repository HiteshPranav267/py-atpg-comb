"""Structured representation of an Atalanta run.

Every numeric field is Optional and defaults to None. A value is None
precisely when Atalanta did not report it — callers must render that as
"Not reported by Atalanta" rather than substituting 0, which would be
indistinguishable from a genuine zero result.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class AnalysisMode(str, Enum):
    """Which ATPG run(s) an analysis request should perform."""

    #: Fault list narrowed to gates chosen by the LLM/structural heuristic.
    TARGETED = "targeted"
    #: Plain Atalanta over the full collapsed fault list.
    NON_TARGETED = "non_targeted"
    #: Both of the above, plus a comparison.
    COMPARE = "compare"

    @property
    def label(self) -> str:
        return {
            AnalysisMode.TARGETED: "LLM Heuristic (Targeted)",
            AnalysisMode.NON_TARGETED: "Atalanta (Non-Targeted)",
            AnalysisMode.COMPARE: "Compare Both",
        }[self]

    @property
    def runs_targeted(self) -> bool:
        return self in (AnalysisMode.TARGETED, AnalysisMode.COMPARE)

    @property
    def runs_non_targeted(self) -> bool:
        return self in (AnalysisMode.NON_TARGETED, AnalysisMode.COMPARE)


@dataclass
class CircuitStructure:
    """Section 1 of Atalanta's summary: circuit structure as the tool saw it."""

    name: str | None = None
    primary_inputs: int | None = None
    primary_outputs: int | None = None
    gates: int | None = None
    logic_depth: int | None = None


@dataclass
class AtpgParameters:
    """Section 2 of Atalanta's summary: the parameters the run used."""

    generation_mode: str | None = None
    random_pattern_limit: int | None = None
    backtrack_limit: int | None = None
    backtrack_limit_phase1: int | None = None
    backtrack_limit_phase2: int | None = None
    random_seed: int | None = None
    compaction_mode: str | None = None
    shuffling_compaction_limit: int | None = None
    shuffles: int | None = None


@dataclass
class AtpgResults:
    """Section 3 of Atalanta's summary: the generation results."""

    patterns_before_compaction: int | None = None
    patterns_after_compaction: int | None = None
    fault_coverage: float | None = None
    collapsed_faults: int | None = None
    redundant_faults: int | None = None
    aborted_faults: int | None = None
    backtrackings: int | None = None

    @property
    def detected_faults(self) -> int | None:
        """Faults Atalanta detected.

        Derived as collapsed - redundant - aborted, which is exactly how
        Atalanta itself computes the aborted count (see print.cpp). Returns
        None if any operand is missing rather than guessing.
        """
        if self.collapsed_faults is None:
            return None
        if self.redundant_faults is None or self.aborted_faults is None:
            return None
        return self.collapsed_faults - self.redundant_faults - self.aborted_faults


@dataclass
class TimingBreakdown:
    """Section 5 of Atalanta's summary: CPU time, in seconds."""

    initialization: float | None = None
    fault_simulation: float | None = None
    fan: float | None = None
    total: float | None = None


@dataclass
class FaultEntry:
    """A single fault from Atalanta's undetected-fault (-U) report."""

    fault_name: str
    gate: str
    fault_type: str
    status: str


@dataclass
class TestVector:
    """One generated test pattern and its fault-free response."""

    index: int
    inputs: str
    outputs: str


@dataclass
class TestVectorSet:
    """Parsed contents of an Atalanta .vec file."""

    primary_inputs: list[str] = field(default_factory=list)
    primary_outputs: list[str] = field(default_factory=list)
    vectors: list[TestVector] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.vectors)


@dataclass
class AtalantaRun:
    """Everything produced by one invocation of Atalanta."""

    label: str
    targeted: bool
    tool: str = "Atalanta"
    version: str | None = None
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    circuit: CircuitStructure = field(default_factory=CircuitStructure)
    parameters: AtpgParameters = field(default_factory=AtpgParameters)
    results: AtpgResults = field(default_factory=AtpgResults)
    timing: TimingBreakdown = field(default_factory=TimingBreakdown)
    memory_mb: float | None = None
    faults: list[FaultEntry] = field(default_factory=list)
    test_vectors: TestVectorSet = field(default_factory=TestVectorSet)
    targeted_gates: list[str] = field(default_factory=list)
    raw_log: str = ""
    raw_stderr: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results"]["detected_faults"] = self.results.detected_faults
        data["test_vectors"]["count"] = self.test_vectors.count
        return data


@dataclass
class RunComparison:
    """Targeted vs non-targeted, computed only from values both runs reported."""

    targeted_coverage: float | None = None
    non_targeted_coverage: float | None = None
    coverage_delta: float | None = None
    targeted_circuit_wide_coverage: float | None = None
    targeted_patterns: int | None = None
    non_targeted_patterns: int | None = None
    pattern_reduction_pct: float | None = None
    targeted_runtime: float | None = None
    non_targeted_runtime: float | None = None
    notes: list[str] = field(default_factory=list)


def build_comparison(targeted: AtalantaRun, non_targeted: AtalantaRun) -> RunComparison:
    """Compare two runs, leaving any metric absent from either side as None."""
    t_cov = targeted.results.fault_coverage
    n_cov = non_targeted.results.fault_coverage
    t_pat = targeted.results.patterns_after_compaction
    n_pat = non_targeted.results.patterns_after_compaction

    comparison = RunComparison(
        targeted_coverage=t_cov,
        non_targeted_coverage=n_cov,
        targeted_patterns=t_pat,
        non_targeted_patterns=n_pat,
        targeted_runtime=targeted.timing.total,
        non_targeted_runtime=non_targeted.timing.total,
    )

    if t_cov is not None and n_cov is not None:
        comparison.coverage_delta = round(t_cov - n_cov, 3)
    if t_pat is not None and n_pat is not None and n_pat > 0:
        comparison.pattern_reduction_pct = round(((n_pat - t_pat) / n_pat) * 100, 2)

    # t_cov alone can't be read against n_cov: it's detected/collapsed over
    # only the targeted gates' faults, while n_cov is over the whole circuit.
    # Re-express the targeted run's detections as a share of the circuit's
    # full collapsed fault list, so it can be read alongside n_cov honestly
    # (it will typically be small -- targeting a handful of gates was never
    # meant to approach full-circuit coverage on its own).
    t_detected = targeted.results.detected_faults
    n_collapsed = non_targeted.results.collapsed_faults
    if t_detected is not None and n_collapsed:
        comparison.targeted_circuit_wide_coverage = round(
            (t_detected / n_collapsed) * 100, 3
        )

    # The two runs target different fault sets, so coverage percentages are
    # not measuring the same denominator. Say so rather than letting the
    # numbers imply a like-for-like improvement.
    comparison.notes.append(
        "Coverage percentages are relative to each run's own fault list: the "
        "targeted run collapses only faults on selected gates, the "
        "non-targeted run collapses the whole circuit. Compare targeted_coverage "
        "and non_targeted_coverage as different experiments, not as a single "
        "improving metric. targeted_circuit_wide_coverage instead expresses the "
        "targeted run's detections as a share of the whole circuit's collapsed "
        "faults, for an apples-to-apples comparison against non_targeted_coverage."
    )
    return comparison
