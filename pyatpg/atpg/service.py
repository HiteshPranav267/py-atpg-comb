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
from .models import AnalysisMode, AtalantaRun, RunComparison, SequentialSummary, build_comparison
from .runner import AtalantaRunner, build_fault_lines
from .structural import ParsedCircuit, parse_circuit, profile_circuit
from .workspace import JobWorkspace, prune_old_jobs

#: Environment variable holding the Gemini credential.
GEMINI_KEY_ENV_VAR = "GEMINI_API_KEY"


#: Stand-in written over a credential before any text reaches a response.
_REDACTED = "[redacted]"


class NoTargetableGates(RuntimeError):
    """Targeting selected no gates, so a targeted run is not meaningful."""


def _redact_secret(analysis: "AtpgAnalysis", secret: str) -> None:
    """Remove a credential from every free-text field of a result."""

    def scrub(value: str) -> str:
        return value.replace(secret, _REDACTED) if secret in value else value

    analysis.notes = [scrub(note) for note in analysis.notes]
    if analysis.targeting.get("notes"):
        analysis.targeting["notes"] = [scrub(n) for n in analysis.targeting["notes"]]
    if analysis.circuit_analysis is not None:
        analysis.circuit_analysis.reasoning = [
            scrub(r) for r in analysis.circuit_analysis.reasoning
        ]
        if analysis.circuit_analysis.unavailable_reason:
            analysis.circuit_analysis.unavailable_reason = scrub(
                analysis.circuit_analysis.unavailable_reason
            )
    if analysis.sequential is not None:
        analysis.sequential.analysis["notes"] = [
            scrub(n) for n in analysis.sequential.analysis.get("notes", [])
        ]
        summary = analysis.sequential.llm_usage.get("circuit_summary")
        if isinstance(summary, str):
            analysis.sequential.llm_usage["circuit_summary"] = scrub(summary)


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
    #: Populated instead of `runs`/`targeting`/`circuit_analysis` when the
    #: circuit has flip-flops -- Atalanta cannot levelize sequential logic,
    #: so those circuits are routed to pyatpg.seq instead of rejected.
    sequential: SequentialSummary | None = None

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
            "sequential": self.sequential.to_dict() if self.sequential else None,
        }


class AtpgAnalysisService:
    """Runs an ATPG analysis end to end."""

    #: Keys the runs dict uses, matching the two selectable run types.
    TARGETED_KEY = "targeted"
    NON_TARGETED_KEY = "non_targeted"

    def __init__(self, runner: AtalantaRunner | None = None, api_key: str | None = None):
        self.runner = runner or AtalantaRunner()
        self._explicit_api_key = api_key

    def _api_key(self, use_llm: bool, request_key: str | None = None) -> str | None:
        """Resolve the credential for this request.

        A key supplied with the request wins, so it can be changed without
        restarting the service; otherwise the deployment's environment
        variable is used. Never logged or echoed back in a response.
        """
        if not use_llm:
            return None
        candidates = (request_key, self._explicit_api_key, os.environ.get(GEMINI_KEY_ENV_VAR))
        for candidate in candidates:
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    def run(
        self,
        source_text: str,
        filename: str,
        mode: AnalysisMode = AnalysisMode.COMPARE,
        toggle_patterns: int = 3000,
        use_llm: bool = True,
        api_key: str | None = None,
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
            api_key: credential for this request only, taking precedence over
                the deployment's environment variable.
        """
        prune_old_jobs()

        parsed = parse_circuit(source_text, filename)

        if parsed.circuit.flipflops:
            # Atalanta is a purely combinational engine; it cannot levelize a
            # netlist with state elements. Route to the native sequential
            # engine (pyatpg.seq) instead of rejecting the circuit.
            return self._run_sequential(parsed, source_text, filename, mode, use_llm, api_key)

        profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
        if not profile.statistics.get("is_dag", True):
            raise ValueError(
                "This netlist contains a combinational feedback loop (it is not a "
                "DAG) with no flip-flop breaking it, so the ATPG tool cannot "
                "levelize or generate patterns for it."
            )
        api_key = self._api_key(use_llm, request_key=api_key)
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

        # Provider errors are surfaced verbatim to explain a fallback. Scrub
        # the credential from them centrally, so no provider that happens to
        # echo the key back can put it in a response.
        if api_key:
            _redact_secret(analysis, api_key)
        return analysis

    def _run_sequential(
        self,
        parsed: ParsedCircuit,
        source_text: str,
        filename: str,
        mode: AnalysisMode,
        use_llm: bool,
        api_key: str | None,
    ) -> AtpgAnalysis:
        """Sequential counterpart of ``run()``, delegating to pyatpg.seq.

        Kept as a thin adapter so the response envelope (job_id, notes,
        redaction) stays identical between the two paths even though the
        underlying engines are unrelated. ``mode`` is repurposed the same
        way it gates the combinational path: NON_TARGETED means "no LLM
        assistance", TARGETED/COMPARE mean "use it if a key is configured" --
        there is no separate notion of a targeted fault subset here, since
        LLM-2 already only ever prioritizes among the faults still
        undetected after deterministic search, for every mode.
        """
        from pyatpg.seq.service import SequentialAtpgService

        workspace = JobWorkspace.create(parsed.name)
        resolved_key = self._api_key(use_llm, request_key=api_key)
        effective_use_llm = use_llm and mode.runs_targeted

        notes = [
            "Sequential circuit: routed to the native time-frame-expansion ATPG "
            "engine for circuits with flip-flops."
        ]
        seq_service = SequentialAtpgService(api_key=self._explicit_api_key)

        if mode is AnalysisMode.COMPARE and use_llm and resolved_key:
            # A real deterministic-only-vs-LLM-assisted comparison: two full
            # runs, same seed, so the difference is attributable to the LLM.
            comp = seq_service.run_comparison(
                source_text=source_text, filename=filename, api_key=api_key
            )
            notes.extend(comp.comparison.notes)
            sequential = SequentialSummary(
                circuit=comp.circuit,
                analysis=comp.comparison.llm_assisted.to_dict(),
                llm_usage=comp.llm_usage,
                comparison=comp.comparison.to_dict(),
            )
            circuit_payload = comp.circuit
        else:
            if mode is AnalysisMode.COMPARE and use_llm and not resolved_key:
                notes.append(
                    "Compare mode requested but no API key is configured, so there is "
                    "nothing to compare against; ran deterministic-only ATPG. Add a "
                    "key in Settings for a deterministic-vs-LLM-assisted comparison."
                )
            elif use_llm and not mode.runs_targeted:
                notes.append(
                    "LLM assistance skipped: mode is set to non-targeted "
                    "(deterministic-only ATPG). Switch to a targeted or compare mode "
                    "to enable it."
                )
            elif effective_use_llm and not resolved_key:
                notes.append(
                    "LLM assistance requested but no API key is configured; ran "
                    "deterministic-only ATPG. Add a key in Settings to enable it."
                )

            result = seq_service.run(
                source_text=source_text,
                filename=filename,
                use_llm=effective_use_llm,
                api_key=api_key,
            )
            llm2_calls = result.llm_usage.get("by_stage", {}).get("llm2", {}).get("calls", 0)
            if effective_use_llm and resolved_key and result.llm_usage.get("enabled") and not llm2_calls:
                notes.append(
                    "LLM-1 summarised the circuit, but LLM-2 fault prioritization was "
                    "never consulted: deterministic search (random-fill + PODEM) already "
                    "reached every fault it could detect, so there was nothing left for "
                    "it to help with."
                )
            sequential = SequentialSummary(
                circuit=result.circuit,
                analysis=result.analysis.to_dict(),
                llm_usage=result.llm_usage,
            )
            circuit_payload = result.circuit

        analysis = AtpgAnalysis(
            job_id=workspace.job_id,
            mode=mode,
            circuit=circuit_payload,
            sequential=sequential,
            notes=notes,
        )
        if resolved_key:
            _redact_secret(analysis, resolved_key)
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
