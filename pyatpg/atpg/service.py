"""Orchestration of a complete ATPG analysis.

Ties together circuit parsing, structural profiling, fault targeting,
Atalanta execution, interpretive analysis and correlation into the single
structured result the API returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .circuit_analyst import CircuitAnalysis, CircuitAnalyst
from .correlation import CorrelationReport, correlate
from .faultsim import simulate_pattern_coverage
from .verdict import TrojanVerdict, build_verdict
from .models import AnalysisMode, AtalantaRun, RunComparison, SequentialSummary, build_comparison
from .runner import AtalantaRunner, build_fault_lines
from .structural import ParsedCircuit, parse_circuit, profile_circuit
from .workspace import JobWorkspace, prune_old_jobs

from pyatpg.llm.token_tracker import TokenTracker
from pyatpg.ranking.providers import LLMConfig, create_provider, resolve_llm_config


#: Stand-in written over a credential before any text reaches a response.
_REDACTED = "[redacted]"

#: Search budgets tried, in turn, for a targeted run that still has aborted
#: faults after the first attempt, as (phase-1 backtracks, phase-2
#: backtracks, static learning). The targeted fault list is a few percent of
#: the circuit, so even the top rung costs well under a second on the ISCAS
#: circuits -- and the faults in it were chosen *because* they are hard, so
#: the default budget of 10 backtracks is exactly where they abort. The
#: non-targeted run is left at the budget the caller asked for: it is the
#: plain-ATPG baseline, and this ladder over a whole circuit is not cheap.
TARGETED_EFFORT_LADDER: tuple[tuple[int, int, bool], ...] = (
    (1000, 1000, False),
    (10000, 10000, True),
)

#: Wall-clock allowance for fault-simulating one run's patterns over the
#: whole circuit. Well above what the ISCAS-85 set needs (about a second on
#: c6288); a circuit that blows through it reports the figure as unavailable
#: rather than holding up the response.
PATTERN_SIMULATION_BUDGET_SECONDS = 30.0


class NoTargetableGates(RuntimeError):
    """Targeting selected no gates, so a targeted run is not meaningful."""


def _targeted_scope_note(run: AtalantaRun, gates: list[str]) -> str:
    """Spell out what a targeted run's fault coverage is a percentage *of*.

    Atalanta computes coverage over the fault list it was given. With -f that
    list holds only the selected gates' stuck-at faults, so a targeted run
    routinely reports 99-100% while the great majority of the circuit's faults
    were never in the list at all. Presented as a bare "fault coverage" it
    reads as "the circuit is fully tested", which is close to the opposite of
    what a targeted run measures. Compare mode already carries this caveat via
    build_comparison; a targeted-only run carried nothing.
    """
    collapsed = run.results.collapsed_faults
    scope = (
        f"{collapsed} collapsed fault(s) from the {len(gates)} selected gate(s)"
        if collapsed is not None
        else f"the {len(gates)} selected gate(s)' faults"
    )
    return (
        f"This was a targeted run: its fault coverage is measured over {scope}, "
        "not over the whole circuit. A short fault list is easy to detect in "
        "full, so a high percentage here does not mean the circuit is well "
        "tested -- it means the selected faults were detectable. Use the "
        "compare mode to see this alongside whole-circuit coverage."
    )


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


def _describe_attempt(
    run: AtalantaRun, phase1: int | None, phase2: int | None, learning: bool
) -> dict[str, Any]:
    """One row of a targeted run's search-budget history."""
    parts = [f"-b {phase1}" if phase1 is not None else "default budget"]
    if phase2 is not None:
        parts.append(f"-B {phase2}")
    if learning:
        parts.append("-L")
    return {
        "label": " ".join(parts),
        "backtrack_limit": phase1,
        "backtrack_limit_phase2": phase2,
        "static_learning": learning,
        "fault_coverage": run.results.fault_coverage,
        "aborted_faults": run.results.aborted_faults,
        "redundant_faults": run.results.redundant_faults,
        "patterns": run.results.patterns_after_compaction,
        "cpu_seconds": run.timing.total,
    }


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
    #: The run's trojan conclusion. Separate from `correlation`, which only
    #: reports overlap between two lists and deliberately draws no conclusion.
    verdict: TrojanVerdict | None = None
    notes: list[str] = field(default_factory=list)
    #: Populated instead of `runs`/`targeting`/`circuit_analysis` when the
    #: circuit has flip-flops -- Atalanta cannot levelize sequential logic,
    #: so those circuits are routed to pyatpg.seq instead of rejected.
    sequential: SequentialSummary | None = None
    #: Every model call this analysis made, with token counts (the
    #: provider's own where it reported them), per stage. ``enabled`` is
    #: False when no model was consulted at all.
    llm_usage: dict[str, Any] = field(default_factory=lambda: {"enabled": False})

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
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "notes": self.notes,
            "sequential": self.sequential.to_dict() if self.sequential else None,
            "llm_usage": self.llm_usage,
        }


class AtpgAnalysisService:
    """Runs an ATPG analysis end to end."""

    #: Keys the runs dict uses, matching the two selectable run types.
    TARGETED_KEY = "targeted"
    NON_TARGETED_KEY = "non_targeted"

    def __init__(
        self,
        runner: AtalantaRunner | None = None,
        api_key: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
    ):
        self.runner = runner or AtalantaRunner()
        self._explicit_api_key = api_key
        self._explicit_provider = llm_provider
        self._explicit_model = llm_model
        self._explicit_base_url = llm_base_url

    def _llm_config(
        self,
        use_llm: bool,
        request_key: str | None = None,
        request_provider: str | None = None,
        request_model: str | None = None,
        request_base_url: str | None = None,
    ) -> LLMConfig | None:
        """Resolve which model (and credential) this request should use.

        Values supplied with the request win, so a provider or key can be
        changed without restarting the service; otherwise the values the
        service was built with, then the deployment's environment variables
        (``LLM_PROVIDER`` / ``LLM_API_KEY`` or a vendor-specific key such as
        ``OPENAI_API_KEY``). The key is never logged or echoed back.
        """
        if not use_llm:
            return None
        return resolve_llm_config(
            provider=request_provider,
            api_key=request_key,
            model=request_model,
            base_url=request_base_url,
            fallback_api_key=self._explicit_api_key,
            fallback_provider=self._explicit_provider,
            fallback_model=self._explicit_model,
            fallback_base_url=self._explicit_base_url,
        )

    def run(
        self,
        source_text: str,
        filename: str,
        mode: AnalysisMode = AnalysisMode.COMPARE,
        toggle_patterns: int = 3000,
        use_llm: bool = True,
        api_key: str | None = None,
        backtrack_limit: int | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
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
                Both layers use deterministic structural methods when this
                is False or no key is configured. When a key *is* configured
                but the provider cannot be run -- out of credits, rate
                limited (429), a 5xx from the service, a rejected key -- the
                run is aborted with :class:`LLMUnavailableError` rather than
                silently substituting the structural heuristic; the message
                says why the model could not be consulted.
            api_key: credential for this request only, taking precedence over
                the deployment's environment variable.
            llm_provider: which vendor the key belongs to (``gemini``,
                ``openai``, ``anthropic``, ``nvidia``, ``openai_compatible``);
                None/"auto" sniffs it from the key's prefix.
            llm_model: model id override; each provider has a sensible default.
            llm_base_url: endpoint override for OpenAI-compatible providers.
            backtrack_limit: Atalanta's own search-effort cap (its -b flag),
                applied to every run this call makes. None leaves Atalanta's
                built-in default (10) in effect. Raising it lets a fault that
                aborted for lack of search budget be told apart from one that
                is genuinely undetectable -- see AtalantaRunner.run.
        """
        prune_old_jobs()

        parsed = parse_circuit(source_text, filename)

        if parsed.circuit.flipflops:
            # Atalanta is a purely combinational engine; it cannot levelize a
            # netlist with state elements. Route to the native sequential
            # engine (pyatpg.seq) instead of rejecting the circuit.
            return self._run_sequential(
                parsed, source_text, filename, mode, use_llm,
                self._llm_config(use_llm, api_key, llm_provider, llm_model, llm_base_url),
            )

        profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
        if not profile.statistics.get("is_dag", True):
            raise ValueError(
                "This netlist contains a combinational feedback loop (it is not a "
                "DAG) with no flip-flop breaking it, so the ATPG tool cannot "
                "levelize or generate patterns for it."
            )
        llm_config = self._llm_config(use_llm, api_key, llm_provider, llm_model, llm_base_url)
        # One provider instance serves both the target selector and the
        # analyst, so a single request never authenticates twice.
        provider = None
        llm_unavailable_reason = ""
        if llm_config is not None:
            try:
                provider = create_provider(llm_config)
            except ImportError as exc:
                # SDK for the chosen vendor is not installed on this
                # deployment; run without the LLM and say why.
                provider = None
                llm_unavailable_reason = str(exc)
        workspace = JobWorkspace.create(parsed.name)
        bench_text = parsed.to_bench()

        analysis = AtpgAnalysis(
            job_id=workspace.job_id,
            mode=mode,
            circuit=self._circuit_payload(parsed, profile.statistics),
        )
        if parsed.implicit_inputs:
            names = ", ".join(parsed.implicit_inputs)
            analysis.notes.append(
                f"This netlist references signal(s) that are never driven by any gate "
                f"({names}) -- treated as free primary inputs, since an undriven net's "
                f"value is exactly as unconstrained as a real one's."
            )

        # Structural trojan evidence is independent of which ATPG runs were
        # requested, so compute it once here: a non-targeted run should still
        # get a verdict, and the targeted path reuses the same finding.
        from .suspects import find_structural_suspects

        try:
            structural_suspects, suspect_reasons = find_structural_suspects(
                profile.gate_profiles, profile.graph
            )
        except Exception:  # noqa: BLE001 - detector failure must not fail the run
            structural_suspects, suspect_reasons = [], []

        # One tracker across both model stages, so the response can say
        # exactly what the whole request cost.
        tracker = TokenTracker()

        selection = None
        if mode.runs_targeted:
            from .targeting import FaultTargetSelector

            selector = FaultTargetSelector(provider=provider, tracker=tracker)
            selection = selector.select(
                profile.gate_profiles,
                summary="",
                graph=profile.graph,
                source_text=parsed.source_text,
            )
            analysis.targeting = {
                "strategy": selection.strategy,
                "used_llm": selection.used_llm,
                "cached": selection.cached,
                "context": selection.context,
                "gates": selection.gates,
                "gate_count": len(selection.gates),
                "notes": selection.notes,
                "structural_suspects": selection.structural_suspects,
                "suspect_reasons": selection.suspect_reasons,
                # The ranking the selection was made from is only reproducible
                # against the stimulus it was measured with, so record it.
                "toggle_seed": profile.toggle_seed,
                "toggle_patterns": profile.toggle_patterns,
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
                backtrack_limit=backtrack_limit,
            )

        if selection is not None:
            analysis.runs[self.TARGETED_KEY] = self._run_targeted(
                workspace, bench_text, selection.gates, backtrack_limit, analysis.notes
            )

        # Score every run's patterns over the whole circuit, on one shared
        # fault list, so a targeted run's patterns can be read against the
        # circuit and not only against the handful of faults they were
        # written for.
        for run in analysis.runs.values():
            run.pattern_coverage = self._score_patterns(parsed, run)

        if self.TARGETED_KEY in analysis.runs and self.NON_TARGETED_KEY in analysis.runs:
            analysis.comparison = build_comparison(
                analysis.runs[self.TARGETED_KEY],
                analysis.runs[self.NON_TARGETED_KEY],
            )
        elif self.TARGETED_KEY in analysis.runs:
            # A targeted-only run has no non-targeted run to build a comparison
            # against, so none of build_comparison's scope caveats exist -- and
            # its fault coverage is the one number a reader is most likely to
            # misread, because it is a percentage of the selected gates' faults,
            # not of the circuit's. Say so on the run itself.
            analysis.notes.append(
                _targeted_scope_note(analysis.runs[self.TARGETED_KEY], selection.gates)
            )

        analyst = CircuitAnalyst(provider=provider, tracker=tracker)
        analysis.circuit_analysis = analyst.analyze(
            circuit_source=parsed.source_text,
            profiles=profile.gate_profiles,
            stats=profile.statistics,
            circuit_name=parsed.name,
        )
        analysis.correlation = correlate(
            analysis.circuit_analysis, list(analysis.runs.values())
        )
        analysis.verdict = build_verdict(
            structural_suspects,
            suspect_reasons,
            list(analysis.runs.values()),
            targeted_gates=(selection.gates if selection else []),
        )

        # Provider errors are surfaced verbatim to explain a fallback. Scrub
        # the credential from them centrally, so no provider that happens to
        # echo the key back can put it in a response.
        if llm_config is not None:
            if provider is None:
                analysis.notes.append(
                    f"LLM assistance unavailable ({llm_unavailable_reason}); "
                    "ran with deterministic structural methods instead."
                )
            else:
                analysis.notes.append(f"LLM: {llm_config.describe()}.")
                analysis.llm_usage = {
                    "enabled": True,
                    "provider": llm_config.spec.label,
                    "model": llm_config.model,
                    **tracker.summary(),
                }
            _redact_secret(analysis, llm_config.api_key)
        return analysis

    def _run_targeted(
        self,
        workspace: JobWorkspace,
        bench_text: str,
        gates: list[str],
        backtrack_limit: int | None,
        notes: list[str],
    ) -> AtalantaRun:
        """Run the targeted fault list, raising the search budget while faults abort.

        An aborted fault is one Atalanta gave up on, not one it proved
        undetectable; on a list built from the hardest faults in the
        circuit, the default budget leaves several of those behind and the
        targeted run's own coverage reads worse than the full run's for no
        reason but budget. Each rung of :data:`TARGETED_EFFORT_LADDER` is
        tried only while the previous attempt still aborted something, and
        the attempt with the fewest aborted faults is the one reported. Every
        attempt is recorded on the run so the reader can see what it cost.
        """
        kwargs: dict[str, Any] = dict(
            label=AnalysisMode.TARGETED.label,
            targeted_faults=build_fault_lines(gates),
            targeted_gates=gates,
        )
        best = self.runner.run(workspace, bench_text, backtrack_limit=backtrack_limit, **kwargs)
        attempts = [_describe_attempt(best, backtrack_limit, None, False)]
        best_index = 0
        first_aborted = best.results.aborted_faults or 0

        for phase1, phase2, learning in TARGETED_EFFORT_LADDER:
            if not best.results.aborted_faults:
                break
            if backtrack_limit is not None and phase1 <= backtrack_limit:
                # The caller already asked for at least this much effort.
                continue
            attempt = self.runner.run(
                workspace,
                bench_text,
                backtrack_limit=phase1,
                backtrack_limit_phase2=phase2,
                static_learning=learning,
                attempt_tag=f"b{phase1}",
                **kwargs,
            )
            attempts.append(_describe_attempt(attempt, phase1, phase2, learning))
            if (attempt.results.aborted_faults or 0) < (best.results.aborted_faults or 0):
                best = attempt
                best_index = len(attempts) - 1

        if len(attempts) > 1:
            for index, attempt_row in enumerate(attempts):
                attempt_row["reported"] = index == best_index
            best.attempts = attempts
            remaining = best.results.aborted_faults or 0
            outcome = (
                "every fault in the list was resolved"
                if remaining == 0
                else f"{remaining} still aborted"
            )
            tried = ", ".join(a["label"] for a in attempts[1:])
            notes.append(
                f"Targeted run: {first_aborted} fault(s) aborted at the initial search "
                f"budget, so the same fault list was re-run at higher backtrack limits "
                f"({tried}); {outcome}. The reported targeted run is the attempt with "
                "the fewest aborted faults. The non-targeted run keeps the requested "
                "budget, as the plain-ATPG baseline."
            )
        return best

    @staticmethod
    def _score_patterns(parsed: ParsedCircuit, run: AtalantaRun) -> dict[str, Any]:
        try:
            result = simulate_pattern_coverage(
                parsed.circuit,
                run.test_vectors,
                time_budget_s=PATTERN_SIMULATION_BUDGET_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - a scoring failure must not fail the run
            return {
                "total_faults": 0,
                "detected_faults": 0,
                "patterns": run.test_vectors.count,
                "coverage": None,
                "unavailable_reason": f"Pattern simulation failed: {exc}",
                "notes": [],
            }
        return result.to_dict()

    def _run_sequential(
        self,
        parsed: ParsedCircuit,
        source_text: str,
        filename: str,
        mode: AnalysisMode,
        use_llm: bool,
        llm_config: LLMConfig | None,
    ) -> AtpgAnalysis:
        """Sequential counterpart of ``run()``, delegating to pyatpg.seq.

        Kept as a thin adapter so the response envelope (job_id, notes,
        redaction) stays identical between the two paths even though the
        underlying engines are unrelated. ``mode`` gates LLM involvement the
        same way it does on the combinational path: NON_TARGETED means "no
        LLM assistance". Unlike combinational, TARGETED and COMPARE behave
        identically here -- there is no separate notion of a targeted fault
        subset for a sequential run, so whenever LLM assistance is on, the
        deterministic-only-vs-LLM-assisted comparison always runs and is
        always shown. Splitting that into two different modes just meant a
        user had to already know to pick "Compare" to see the comparison
        they wanted by asking for "Targeted" in the first place.
        """
        from pyatpg.seq.service import SequentialAtpgService

        workspace = JobWorkspace.create(parsed.name)
        effective_use_llm = use_llm and mode.runs_targeted

        notes = [
            "Sequential circuit: routed to the native time-frame-expansion ATPG "
            "engine for circuits with flip-flops."
        ]
        seq_service = SequentialAtpgService()

        if effective_use_llm and llm_config is not None:
            # A real deterministic-only-vs-LLM-assisted comparison: two full
            # runs, same seed, so the difference is attributable to the LLM.
            comp = seq_service.run_comparison(
                source_text=source_text, filename=filename, llm_config=llm_config
            )
            notes.append(f"LLM: {llm_config.describe()}.")
            notes.extend(comp.comparison.notes)
            sequential = SequentialSummary(
                circuit=comp.circuit,
                analysis=comp.comparison.llm_assisted.to_dict(),
                llm_usage=comp.llm_usage,
                comparison=comp.comparison.to_dict(),
            )
            circuit_payload = comp.circuit
        else:
            if use_llm and not mode.runs_targeted:
                notes.append(
                    "LLM assistance skipped: mode is set to non-targeted "
                    "(deterministic-only ATPG). Switch to a targeted or compare mode "
                    "to enable it."
                )
            elif effective_use_llm and llm_config is None:
                notes.append(
                    "LLM assistance requested but no API key is configured; ran "
                    "deterministic-only ATPG. Add a key in Settings to see a "
                    "deterministic-vs-LLM-assisted comparison."
                )

            result = seq_service.run(
                source_text=source_text,
                filename=filename,
                use_llm=effective_use_llm,
                llm_config=llm_config,
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
        if llm_config is not None:
            _redact_secret(analysis, llm_config.api_key)
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
