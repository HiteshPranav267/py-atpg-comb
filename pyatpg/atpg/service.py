"""Orchestration of a complete ATPG analysis.

Ties together circuit parsing, structural profiling, fault targeting,
Atalanta execution, interpretive analysis and correlation into the single
structured result the API returns.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .circuit_analyst import CircuitAnalysis, CircuitAnalyst
from .correlation import CorrelationReport, correlate
from .models import AnalysisMode, AtalantaRun, RunComparison, build_comparison
from .runner import AtalantaRunner, build_fault_lines
from .structural import ParsedCircuit, parse_circuit, profile_circuit
from .workspace import JobWorkspace, prune_old_jobs

#: Environment variable holding the Gemini credential.
GEMINI_KEY_ENV_VAR = "GEMINI_API_KEY"


class NoTargetableGates(RuntimeError):
    """Targeting selected no gates, so a targeted run is not meaningful."""


@dataclass
class AtpgAnalysis:
    """The complete result of an analysis request."""

    job_id: str
    mode: AnalysisMode
    circuit: dict[str, Any]
    runs: dict[str, AtalantaRun] = field(default_factory=dict)
    comparison: RunComparison | None = None
    targeting: dict[str, Any] = field(default_factory=dict)
    circuit_analysis: CircuitAnalysis | None = None
    correlation: CorrelationReport | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "job_id": self.job_id,
            "mode": self.mode.value,
            "mode_label": self.mode.label,
            "circuit": self.circuit,
            "runs": {key: run.to_dict() for key, run in self.runs.items()},
            "comparison": asdict(self.comparison) if self.comparison else None,
            "targeting": self.targeting,
            "circuit_analysis": self.circuit_analysis.to_dict() if self.circuit_analysis else None,
            "correlation": self.correlation.to_dict() if self.correlation else None,
            "notes": self.notes,
        }


class AtpgAnalysisService:
    """Runs an ATPG analysis end to end."""

    #: Keys the runs dict uses, matching the two selectable run types.
    TARGETED_KEY = "targeted"
    NON_TARGETED_KEY = "non_targeted"

    def __init__(self, runner: AtalantaRunner | None = None, api_key: str | None = None):
        self.runner = runner or AtalantaRunner()
        self._explicit_api_key = api_key

    def _api_key(self, use_llm: bool) -> str | None:
        if not use_llm:
            return None
        return self._explicit_api_key or os.environ.get(GEMINI_KEY_ENV_VAR) or None

    def run(
        self,
        source_text: str,
        filename: str,
        mode: AnalysisMode = AnalysisMode.COMPARE,
        toggle_patterns: int = 3000,
        use_llm: bool = True,
    ) -> AtpgAnalysis:
        """Execute an analysis.

        Args:
            source_text: Verilog or BENCH source.
            filename: original filename, used for format detection and naming.
            mode: which run(s) to perform.
            toggle_patterns: random patterns used for toggle-rate profiling.
                This drives the structural analysis only -- Atalanta chooses
                its own pattern counts.
            use_llm: whether to consult the model for targeting and analysis.
                Both layers fall back to deterministic structural methods
                when this is False or no key is configured.
        """
        prune_old_jobs()

        parsed = parse_circuit(source_text, filename)
        if parsed.circuit.flipflops:
            raise ValueError(
                "This tool analyses combinational circuits; the netlist contains "
                f"{len(parsed.circuit.flipflops)} sequential element(s)."
            )

        profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
        api_key = self._api_key(use_llm)
        workspace = JobWorkspace.create(parsed.name)
        bench_text = parsed.to_bench()

        analysis = AtpgAnalysis(
            job_id=workspace.job_id,
            mode=mode,
            circuit=self._circuit_payload(parsed, profile.statistics),
        )

        selection = None
        if mode.runs_targeted:
            from .targeting import FaultTargetSelector

            selector = FaultTargetSelector(api_key=api_key)
            selection = selector.select(profile.gate_profiles, summary="")
            analysis.targeting = {
                "strategy": selection.strategy,
                "used_llm": selection.used_llm,
                "gates": selection.gates,
                "gate_count": len(selection.gates),
                "notes": selection.notes,
            }
            if not selection.gates:
                # A targeted-only request has nothing to run. In compare mode
                # the non-targeted half is still worth delivering, so note the
                # empty selection and carry on rather than failing outright.
                if mode is AnalysisMode.TARGETED:
                    raise NoTargetableGates(
                        "No gates met the targeting criteria, so a targeted ATPG run "
                        "would have an empty fault list. Run the non-targeted mode instead."
                    )
                selection = None
                analysis.notes.append(
                    "No gates met the targeting criteria, so only the non-targeted run "
                    "was performed and there is nothing to compare against."
                )

        if mode.runs_non_targeted:
            analysis.runs[self.NON_TARGETED_KEY] = self.runner.run(
                workspace,
                bench_text,
                label=AnalysisMode.NON_TARGETED.label,
            )

        if selection is not None:
            analysis.runs[self.TARGETED_KEY] = self.runner.run(
                workspace,
                bench_text,
                label=AnalysisMode.TARGETED.label,
                targeted_faults=build_fault_lines(selection.gates),
                targeted_gates=selection.gates,
            )

        if self.TARGETED_KEY in analysis.runs and self.NON_TARGETED_KEY in analysis.runs:
            analysis.comparison = build_comparison(
                analysis.runs[self.TARGETED_KEY],
                analysis.runs[self.NON_TARGETED_KEY],
            )

        analyst = CircuitAnalyst(api_key=api_key)
        analysis.circuit_analysis = analyst.analyze(
            circuit_source=parsed.source_text,
            profiles=profile.gate_profiles,
            stats=profile.statistics,
            circuit_name=parsed.name,
        )
        analysis.correlation = correlate(
            analysis.circuit_analysis, list(analysis.runs.values())
        )
        return analysis

    @staticmethod
    def _circuit_payload(parsed: ParsedCircuit, statistics: dict[str, Any]) -> dict[str, Any]:
        """Structure derived from our own parser.

        Atalanta reports its own view of the same circuit inside each run;
        the two are kept separate rather than merged so a discrepancy stays
        visible instead of being silently reconciled.
        """
        return {
            "name": parsed.name,
            "filename": parsed.filename,
            "format": "bench" if parsed.is_bench else "verilog",
            "primary_inputs": statistics.get("primary_inputs"),
            "primary_outputs": statistics.get("primary_outputs"),
            "gates": statistics.get("gates"),
            "logic_depth": statistics.get("max_level"),
            "total_nodes": statistics.get("total_nodes"),
            "total_edges": statistics.get("total_edges"),
            "gate_types": statistics.get("gate_types", {}),
            "max_fan_in": statistics.get("max_fan_in"),
            "max_fan_out": statistics.get("max_fan_out"),
            "avg_fan_in": statistics.get("avg_fan_in"),
            "avg_fan_out": statistics.get("avg_fan_out"),
            "flip_flops": statistics.get("flip_flops"),
            "is_dag": statistics.get("is_dag"),
        }
