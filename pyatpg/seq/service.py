"""Orchestration entry point for a sequential ATPG analysis.

Mirrors ``pyatpg.atpg.service.AtpgAnalysisService`` in shape (parse ->
profile -> run -> optional LLM layer -> structured result) so the API layer
can call either service depending on whether the circuit has flip-flops,
without needing to know the internals of either.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any

from pyatpg.core.progress import check_cancelled as progress_check_cancelled
from pyatpg.core import settings
from pyatpg.core.progress import report
from pyatpg.atpg.structural import ParsedCircuit, StructuralProfile, parse_circuit, profile_circuit

from .engine import EngineConfig, SequentialAtpgEngine
from .models import SequentialAnalysis, SequentialComparison, build_sequential_comparison
from .timeframe import is_combinational_part_acyclic

#: Same env var the combinational service uses, so one credential configures both.
from pyatpg.ranking.providers import LLMConfig, create_provider, resolve_llm_config

#: A fixed seed for comparison runs, so random-fill/PODEM search identical
#: candidates first in both the baseline and LLM-assisted run -- only the
#: LLM-2-assisted phase (and the deeper local search it unlocks) can differ.
_COMPARISON_SEED = 1234


def _baseline_worker(circuit, name: str, target_coverage: float, max_sequences: int,
                     seed: int, time_budget_s: float) -> SequentialAnalysis:
    """The deterministic side of a comparison, runnable in a worker process.

    Mirrors ``_run_engine(..., use_llm=False, extended_search=True)``. The
    time budget is passed in because a worker process does not see the
    parent's live settings.
    """
    config = EngineConfig(target_coverage=target_coverage, max_sequences=max_sequences,
                          extended_search=True, time_budget_s=time_budget_s)
    config.rng_seed = seed
    analysis = SequentialAtpgEngine(circuit, circuit_name=name, config=config).run()
    analysis.timing["llm_s"] = 0.0
    return analysis


def _parallel_comparison_enabled() -> bool:
    """Run a comparison's two engine passes side by side in two processes?

    Halves its wall-clock time on any multi-core machine. Off by default on
    the hosted backend (Render sets ``RENDER``), where a second Python
    process holding its own copy of the circuit risks the 512 MB limit;
    ``PYATPG_PARALLEL_COMPARE=1``/``0`` overrides either way.
    """
    value = os.environ.get("PYATPG_PARALLEL_COMPARE", "").strip()
    if value:
        return value not in ("0", "false", "no")
    return not os.environ.get("RENDER") and (os.cpu_count() or 1) > 1


@dataclass
class SequentialAnalysisResult:
    circuit: dict
    analysis: SequentialAnalysis
    llm_usage: dict
    #: Structural trojan evidence (same detector as the combinational path).
    structural_suspects: list[str] = field(default_factory=list)
    suspect_reasons: list[str] = field(default_factory=list)
    #: Parse + structural profiling wall time, seconds.
    extraction_s: float = 0.0


@dataclass
class SequentialComparisonResult:
    circuit: dict
    comparison: SequentialComparison
    #: The LLM-assisted run's own usage payload, so the caller can surface
    #: it (API calls, token estimates) without digging into `comparison`.
    llm_usage: dict
    structural_suspects: list[str] = field(default_factory=list)
    suspect_reasons: list[str] = field(default_factory=list)
    extraction_s: float = 0.0


class SequentialAtpgService:
    def __init__(
        self,
        api_key: str | None = None,
        llm_provider: str | None = None,
        toggle_patterns: int = 3000,
    ):
        self._explicit_api_key = api_key
        self._explicit_provider = llm_provider
        self._toggle_patterns = toggle_patterns
        self._extraction_s = 0.0

    @staticmethod
    def _suspects(profile: StructuralProfile) -> tuple[list[str], list[str]]:
        """Structural trojan-shape evidence, as on the combinational path."""
        from pyatpg.atpg.suspects import find_structural_suspects

        try:
            return find_structural_suspects(profile.gate_profiles, profile.graph)
        except Exception:  # noqa: BLE001 - detector failure must not fail the run
            return [], []

    def _llm_config(
        self, use_llm: bool, request_key: str | None, llm_config: LLMConfig | None
    ) -> LLMConfig | None:
        """A caller-resolved config wins; otherwise resolve from key/env."""
        if not use_llm:
            return None
        if llm_config is not None:
            return llm_config
        return resolve_llm_config(
            api_key=request_key,
            fallback_api_key=self._explicit_api_key,
            fallback_provider=self._explicit_provider,
        )

    def _parse(self, source_text: str, filename: str) -> tuple[ParsedCircuit, StructuralProfile]:
        t0 = time.monotonic()
        report("Parsing the circuit (synthesizing RTL first, if needed)")
        parsed = parse_circuit(source_text, filename)
        profile = profile_circuit(parsed, toggle_patterns=self._toggle_patterns)
        self._extraction_s = time.monotonic() - t0
        if not is_combinational_part_acyclic(parsed.circuit):
            raise ValueError(
                "This circuit has a combinational feedback loop that is not broken by "
                "a flip-flop (an asynchronous latch loop); sequential ATPG here assumes "
                "every feedback path passes through a clocked flip-flop."
            )
        return parsed, profile

    @staticmethod
    def _implicit_input_notes(parsed: ParsedCircuit) -> list[str]:
        if not parsed.implicit_inputs:
            return list(parsed.parse_notes)
        names = ", ".join(parsed.implicit_inputs)
        return list(parsed.parse_notes) + [
            f"This netlist references signal(s) that are never driven by any gate or "
            f"flip-flop ({names}) -- treated as free primary inputs, since an undriven "
            f"net's value is exactly as unconstrained as a real one's."
        ]

    @staticmethod
    def _circuit_payload(parsed: ParsedCircuit, profile: StructuralProfile) -> dict[str, Any]:
        return {
            "name": parsed.name,
            "filename": parsed.filename,
            "format": "bench" if parsed.is_bench else "verilog",
            "primary_inputs": profile.statistics.get("primary_inputs"),
            "primary_outputs": profile.statistics.get("primary_outputs"),
            "gates": profile.statistics.get("gates"),
            "flip_flops": profile.statistics.get("flip_flops"),
            # Matches the combinational payload's field name (AtpgCircuitOverview.logic_depth
            # on the frontend) rather than the internal "max_level" stat key.
            "logic_depth": profile.statistics.get("max_level"),
            "gate_types": profile.statistics.get("gate_types"),
            "total_nodes": profile.statistics.get("total_nodes"),
            "total_edges": profile.statistics.get("total_edges"),
            "max_fan_in": profile.statistics.get("max_fan_in"),
            "max_fan_out": profile.statistics.get("max_fan_out"),
            "avg_fan_in": profile.statistics.get("avg_fan_in"),
            "avg_fan_out": profile.statistics.get("avg_fan_out"),
            "is_dag": profile.statistics.get("is_dag"),
        }

    def _run_engine(
        self,
        parsed: ParsedCircuit,
        profile: StructuralProfile,
        source_text: str,
        use_llm: bool,
        api_key: str | None,
        target_coverage: float,
        max_sequences: int,
        rng_seed: int | None = None,
        llm_config: LLMConfig | None = None,
        extended_search: bool = False,
    ) -> tuple[SequentialAnalysis, dict]:
        llm = self._llm_config(use_llm, api_key, llm_config)
        llm_seconds = 0.0
        candidate_ranker = None
        llm_usage: dict = {"enabled": False}

        if use_llm and llm is not None:
            from pyatpg.llm.circuit_context import CircuitContextAnalyzer
            from pyatpg.llm.store import LocalContextStore
            from pyatpg.llm.token_tracker import TokenTracker
            from pyatpg.llm.vector_generator import LLMFaultPrioritizer

            t0 = time.monotonic()
            report("LLM: summarizing the circuit")
            provider = create_provider(llm)
            tracker = TokenTracker()
            summary = CircuitContextAnalyzer(provider, tracker).analyze(
                parsed.name, source_text, profile.statistics
            )
            store = LocalContextStore.build(parsed.circuit)
            prioritizer = LLMFaultPrioritizer(provider, store, tracker, summary)
            llm_seconds += time.monotonic() - t0

            def candidate_ranker(faults, context, _rank=prioritizer.rank):
                nonlocal llm_seconds
                start = time.monotonic()
                try:
                    return _rank(faults, context)
                finally:
                    llm_seconds += time.monotonic() - start

            llm_usage = {"enabled": True, "tracker": tracker, "summary": summary}

        config = EngineConfig(
            target_coverage=target_coverage,
            max_sequences=max_sequences,
            extended_search=extended_search,
            time_budget_s=float(settings.get("seq_time_budget_s")),
        )
        if rng_seed is not None:
            config.rng_seed = rng_seed
        engine = SequentialAtpgEngine(
            parsed.circuit,
            circuit_name=parsed.name,
            config=config,
            candidate_ranker=candidate_ranker,
        )
        analysis = engine.run()
        analysis.timing["llm_s"] = round(llm_seconds, 3)

        usage_payload = {"enabled": llm_usage["enabled"]}
        if llm_usage["enabled"]:
            usage_payload.update(llm_usage["tracker"].summary())
            usage_payload["circuit_summary"] = llm_usage["summary"]
            usage_payload["provider"] = llm.provider
            usage_payload["model"] = llm.model

        return analysis, usage_payload

    def run(
        self,
        source_text: str,
        filename: str,
        target_coverage: float = 100.0,
        max_sequences: int = 60,
        use_llm: bool = True,
        api_key: str | None = None,
        llm_config: LLMConfig | None = None,
    ) -> SequentialAnalysisResult:
        parsed, profile = self._parse(source_text, filename)
        report("Sequential ATPG search")
        analysis, llm_usage = self._run_engine(
            parsed, profile, source_text, use_llm, api_key, target_coverage, max_sequences,
            llm_config=llm_config,
        )
        analysis.notes = self._implicit_input_notes(parsed) + analysis.notes
        suspects, reasons = self._suspects(profile)
        return SequentialAnalysisResult(
            circuit=self._circuit_payload(parsed, profile),
            analysis=analysis,
            llm_usage=llm_usage,
            structural_suspects=suspects,
            suspect_reasons=reasons,
            extraction_s=round(self._extraction_s, 3),
        )

    def run_comparison(
        self,
        source_text: str,
        filename: str,
        target_coverage: float = 100.0,
        max_sequences: int = 60,
        api_key: str | None = None,
        llm_config: LLMConfig | None = None,
    ) -> SequentialComparisonResult:
        """Run deterministic-only and LLM-assisted back to back and diff them.

        Both runs use the same fixed seed, so the comparison isolates what
        LLM-2 (and the deeper local search it unlocks) actually contributed
        rather than differences from random-fill drawing different candidates.
        """
        parsed, profile = self._parse(source_text, filename)
        report("Sequential ATPG: deterministic baseline and LLM-assisted run"
               + (" in parallel" if _parallel_comparison_enabled() else ""))
        # The baseline runs the same extra deep-search phase as the LLM run,
        # in natural fault order, so the comparison measures the LLM's
        # ordering -- not the LLM run simply getting more search effort.
        pool = future = None
        if _parallel_comparison_enabled():
            try:
                pool = ProcessPoolExecutor(max_workers=1)
                future = pool.submit(_baseline_worker, parsed.circuit, parsed.name,
                                     target_coverage, max_sequences, _COMPARISON_SEED,
                                     float(settings.get("seq_time_budget_s")))
            except Exception:  # noqa: BLE001 - no worker process: run serially below
                pool = future = None
        try:
            llm_assisted, llm_usage = self._run_engine(
                parsed, profile, source_text, True, api_key,
                target_coverage, max_sequences, rng_seed=_COMPARISON_SEED,
                llm_config=llm_config,
            )
            baseline = None
            if future is not None:
                while baseline is None:
                    try:
                        baseline = future.result(timeout=1.0)
                    except FuturesTimeout:
                        progress_check_cancelled()
                    except Exception:  # noqa: BLE001 - worker failed: fall back to serial
                        break
            if baseline is None:
                baseline, _baseline_usage = self._run_engine(
                    parsed, profile, source_text, False, None,
                    target_coverage, max_sequences, rng_seed=_COMPARISON_SEED,
                    extended_search=True,
                )
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        comparison = build_sequential_comparison(baseline, llm_assisted)
        if not llm_usage.get("enabled"):
            comparison.notes.insert(
                0,
                "No LLM API key is configured, so the 'LLM-assisted' column ran "
                "deterministic-only too -- add a key in Settings for a real comparison.",
            )
        comparison.notes = self._implicit_input_notes(parsed) + comparison.notes
        suspects, reasons = self._suspects(profile)
        return SequentialComparisonResult(
            circuit=self._circuit_payload(parsed, profile),
            comparison=comparison,
            llm_usage=llm_usage,
            structural_suspects=suspects,
            suspect_reasons=reasons,
            extraction_s=round(self._extraction_s, 3),
        )
