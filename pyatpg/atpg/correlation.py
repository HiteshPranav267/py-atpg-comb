"""Correlation between the interpretive analysis and measured ATPG results.

The only claim this module makes is co-occurrence: a gate the analysis
called out also appears in Atalanta's undetected-fault report. That is an
observation about two lists overlapping, not evidence that the analysis
predicted the result or that anything is wrong with the gate. Wording is
deliberately hedged and no confidence score is invented.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .circuit_analyst import CircuitAnalysis
from .models import AtalantaRun

#: Status values that mean Atalanta did not produce a detecting pattern.
_UNDETECTED_STATUSES = {"undetected", "redundant", "aborted", "untestable"}


@dataclass
class CorrelatedGate:
    """A gate referenced by the analysis that also appears in fault results."""

    gate: str
    analysis_context: list[str] = field(default_factory=list)
    fault_names: list[str] = field(default_factory=list)
    atpg_status: str = "undetected"
    statement: str = ""


@dataclass
class CorrelationReport:
    """Overlap between analysis-referenced gates and undetected faults."""

    correlated: list[CorrelatedGate] = field(default_factory=list)
    analysis_gates_examined: int = 0
    undetected_gates_examined: int = 0
    summary: str = ""
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _context_for(analysis: CircuitAnalysis, gate: str) -> list[str]:
    """Every analysis heading that mentioned this gate."""
    context: list[str] = []
    for observation in analysis.observations:
        if gate in observation.gates:
            context.append(observation.title)
    for region in analysis.suspicious_regions:
        if gate in region.gates:
            context.append(region.name)
    return context


def correlate(analysis: CircuitAnalysis, runs: list[AtalantaRun]) -> CorrelationReport:
    """Intersect analysis gate references with faults Atalanta left undetected."""
    report = CorrelationReport()

    analysis_gates = analysis.referenced_gates()
    report.analysis_gates_examined = len(analysis_gates)

    # gate -> (fault names, statuses) across every run performed.
    fault_index: dict[str, dict[str, Any]] = {}
    for run in runs:
        for fault in run.faults:
            if fault.status not in _UNDETECTED_STATUSES:
                continue
            entry = fault_index.setdefault(fault.gate, {"faults": [], "statuses": set()})
            if fault.fault_name not in entry["faults"]:
                entry["faults"].append(fault.fault_name)
            entry["statuses"].add(fault.status)
    report.undetected_gates_examined = len(fault_index)

    for gate in analysis_gates:
        entry = fault_index.get(gate)
        if not entry:
            continue
        status = ", ".join(sorted(entry["statuses"]))
        context = _context_for(analysis, gate)
        context_text = "; ".join(context) if context else "the circuit analysis"
        report.correlated.append(
            CorrelatedGate(
                gate=gate,
                analysis_context=context,
                fault_names=entry["faults"],
                atpg_status=status,
                statement=(
                    f"Gate {gate} was flagged as structurally interesting under "
                    f"\"{context_text}\", and the ATPG tool separately reported "
                    f"{len(entry['faults'])} fault(s) on it as {status}. These two "
                    "observations are correlated; neither establishes the other."
                ),
            )
        )

    if not analysis_gates:
        report.summary = "The circuit analysis referenced no specific gates, so there was nothing to correlate."
    elif not fault_index:
        report.summary = (
            f"The ATPG tool detected every fault it targeted, so none of the "
            f"{len(analysis_gates)} gate(s) raised by the analysis correspond to an "
            "undetected fault."
        )
    elif report.correlated:
        report.summary = (
            f"{len(report.correlated)} of {len(analysis_gates)} gate(s) raised by the "
            f"analysis also appear among the {len(fault_index)} gate(s) with undetected "
            "faults. Worth a closer look, but the overlap alone is not evidence of a defect."
        )
    else:
        report.summary = (
            f"None of the {len(analysis_gates)} gate(s) raised by the analysis overlap "
            f"with the {len(fault_index)} gate(s) carrying undetected faults."
        )

    report.caveats = [
        "Overlap indicates co-occurrence only. The analysis did not predict, and cannot "
        "confirm, any ATPG outcome.",
        "An undetected or aborted fault may simply mean the backtrack limit was reached, "
        "or that the fault is genuinely redundant.",
        "Fault coverage and every other ATPG metric come from the ATPG tool; the analysis "
        "layer contributes no measurements.",
    ]
    return report
