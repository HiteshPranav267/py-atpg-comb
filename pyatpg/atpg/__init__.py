"""Atalanta ATPG integration.

Atalanta (Dong S. Ha, Virginia Tech) is the ATPG engine; every metric this
package reports is read from its output. Nothing here reimplements or
approximates test pattern generation.
"""
from .binary import AtalantaBinary, AtalantaNotAvailable, resolve_atalanta_binary
from .circuit_analyst import CircuitAnalysis, CircuitAnalyst, Observation, SuspiciousRegion
from .correlation import CorrelationReport, correlate
from .models import (
    AnalysisMode,
    AtalantaRun,
    AtpgParameters,
    AtpgResults,
    CircuitStructure,
    FaultEntry,
    RunComparison,
    TestVector,
    TestVectorSet,
    TimingBreakdown,
)
from .runner import AtalantaExecutionError, AtalantaRunner, build_fault_lines
from .service import AtpgAnalysis, AtpgAnalysisService, NoTargetableGates
from .structural import ParsedCircuit, parse_circuit, profile_circuit
from .targeting import FaultTargetSelector, TargetSelection
from .workspace import JobWorkspace

__all__ = [
    "AnalysisMode",
    "AtalantaBinary",
    "AtalantaExecutionError",
    "AtalantaNotAvailable",
    "AtalantaRun",
    "AtalantaRunner",
    "AtpgAnalysis",
    "AtpgAnalysisService",
    "AtpgParameters",
    "AtpgResults",
    "CircuitAnalysis",
    "CircuitAnalyst",
    "CircuitStructure",
    "CorrelationReport",
    "FaultEntry",
    "FaultTargetSelector",
    "JobWorkspace",
    "NoTargetableGates",
    "Observation",
    "ParsedCircuit",
    "RunComparison",
    "SuspiciousRegion",
    "TargetSelection",
    "TestVector",
    "TestVectorSet",
    "TimingBreakdown",
    "build_fault_lines",
    "correlate",
    "parse_circuit",
    "profile_circuit",
    "resolve_atalanta_binary",
]
