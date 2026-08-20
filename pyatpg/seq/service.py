"""Orchestration entry point for a sequential ATPG analysis.

Mirrors ``pyatpg.atpg.service.AtpgAnalysisService`` in shape (parse ->
profile -> run -> optional LLM layer -> structured result) so the API layer
can call either service depending on whether the circuit has flip-flops,
without needing to know the internals of either.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from pyatpg.atpg.structural import ParsedCircuit, StructuralProfile, parse_circuit, profile_circuit

from .engine import EngineConfig, SequentialAtpgEngine
from .models import SequentialAnalysis
from .timeframe import is_combinational_part_acyclic

#: Same env var the combinational service uses, so one credential configures both.
GEMINI_KEY_ENV_VAR = "GEMINI_API_KEY"


@dataclass
class SequentialAnalysisResult:
    circuit: dict
    analysis: SequentialAnalysis
    llm_usage: dict


class SequentialAtpgService:
    def __init__(self, api_key: str | None = None):
        self._explicit_api_key = api_key

    def _api_key(self, use_llm: bool, request_key: str | None) -> str | None:
        if not use_llm:
            return None
        for candidate in (request_key, self._explicit_api_key, os.environ.get(GEMINI_KEY_ENV_VAR)):
            if candidate and candidate.strip():
                return candidate.strip()
        return None

    def run(
        self,
        source_text: str,
        filename: str,
        target_coverage: float = 100.0,
        max_sequences: int = 60,
        use_llm: bool = True,
        api_key: str | None = None,
    ) -> SequentialAnalysisResult:
        parsed: ParsedCircuit = parse_circuit(source_text, filename)
        profile: StructuralProfile = profile_circuit(parsed)
        if not is_combinational_part_acyclic(parsed.circuit):
            raise ValueError(
                "This circuit has a combinational feedback loop that is not broken by "
                "a flip-flop (an asynchronous latch loop); sequential ATPG here assumes "
                "every feedback path passes through a clocked flip-flop."
            )

        key = self._api_key(use_llm, api_key)
        candidate_ranker = None
        llm_usage: dict = {"enabled": False}

        if use_llm and key:
            from pyatpg.llm.circuit_context import CircuitContextAnalyzer
            from pyatpg.llm.store import LocalContextStore
            from pyatpg.llm.token_tracker import TokenTracker
            from pyatpg.llm.vector_generator import LLMFaultPrioritizer
            from pyatpg.ranking.providers import GeminiProvider

            provider = GeminiProvider(api_key=key)
            tracker = TokenTracker()
            summary = CircuitContextAnalyzer(provider, tracker).analyze(
                parsed.name, source_text, profile.statistics
            )
            store = LocalContextStore.build(parsed.circuit)
            prioritizer = LLMFaultPrioritizer(provider, store, tracker, summary)
            candidate_ranker = prioritizer.rank
            llm_usage = {"enabled": True, "tracker": tracker, "summary": summary}

        engine = SequentialAtpgEngine(
            parsed.circuit,
            circuit_name=parsed.name,
            config=EngineConfig(target_coverage=target_coverage, max_sequences=max_sequences),
            candidate_ranker=candidate_ranker,
        )
        analysis = engine.run()

        usage_payload = {"enabled": llm_usage["enabled"]}
        if llm_usage["enabled"]:
            usage_payload.update(llm_usage["tracker"].summary())
            usage_payload["circuit_summary"] = llm_usage["summary"]

        return SequentialAnalysisResult(
            circuit={
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
            },
            analysis=analysis,
            llm_usage=usage_payload,
        )
