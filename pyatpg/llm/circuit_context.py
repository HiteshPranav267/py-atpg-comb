"""LLM-1: one-time-per-circuit understanding pass.

Reads the circuit-level statistics the rest of the project already
computes (never the raw Verilog, never a per-gate dump) and produces a
short cached summary that LLM-2 calls reuse verbatim instead of
re-describing the circuit on every call. Cached by a hash of the circuit
source text via the same on-disk pattern as ``pyatpg.core.memory``, so a
repeat run against the same netlist costs zero LLM calls.
"""
from __future__ import annotations

import os
from typing import Any

from pyatpg.core.memory import CircuitMemory

from .token_tracker import TokenTracker

try:
    from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError
except ImportError:  # pragma: no cover
    LLMProvider = None  # type: ignore[assignment,misc]

    class LLMUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Placeholder so the except clause below still resolves."""

_DEFAULT_CACHE_PATH = os.path.join(os.getcwd(), "outputs", "seq_llm1_cache.json")


def _structural_summary(circuit_name: str, stats: dict[str, Any]) -> str:
    """No-LLM fallback: a short, deterministic narrative from stats alone."""
    return (
        f"Circuit '{circuit_name}': {stats.get('primary_inputs', '?')} PIs, "
        f"{stats.get('primary_outputs', '?')} POs, {stats.get('flip_flops', '?')} flip-flops, "
        f"{stats.get('gates', '?')} gates, depth {stats.get('max_level', '?')}, "
        f"gate mix {stats.get('gate_types', {})}. "
        f"Avg fan-in {stats.get('avg_fan_in', '?')}, avg fan-out {stats.get('avg_fan_out', '?')}."
    )


class CircuitContextAnalyzer:
    """Produces (and caches) the compact circuit summary LLM-2 reuses."""

    def __init__(
        self,
        provider: "LLMProvider | None",
        tracker: TokenTracker,
        cache: CircuitMemory | None = None,
    ):
        self.provider = provider
        self.tracker = tracker
        self.cache = cache or CircuitMemory(storage_path=_DEFAULT_CACHE_PATH)

    def analyze(self, circuit_name: str, source_text: str, stats: dict[str, Any]) -> str:
        cached = self.cache.find_match(source_text)
        if cached and cached.get("results"):
            self.tracker.record_cached("llm1")
            return cached["results"]

        if self.provider is None:
            summary = _structural_summary(circuit_name, stats)
            self.cache.remember(source_text, summary, stats)
            return summary

        prompt = (
            "Summarise this sequential digital circuit's testability in at most 4 "
            "sentences, for another model that will later pick hard-to-test faults. "
            "Do not invent numbers not given below.\n\n"
            f"Name: {circuit_name}\n"
            f"Primary inputs: {stats.get('primary_inputs')}\n"
            f"Primary outputs: {stats.get('primary_outputs')}\n"
            f"Flip-flops: {stats.get('flip_flops')}\n"
            f"Gates: {stats.get('gates')} ({stats.get('gate_types', {})})\n"
            f"Logic depth: {stats.get('max_level')}\n"
            f"Avg fan-in/out: {stats.get('avg_fan_in')}/{stats.get('avg_fan_out')}\n"
        )
        try:
            summary = self.provider.complete(prompt)
        except LLMUnavailableError:
            # Provider could not be reached at all: the run stops here, it
            # must not continue on the no-LLM summary as if it had been asked.
            raise
        except Exception:  # noqa: BLE001 - model ran but its answer was unusable
            summary = _structural_summary(circuit_name, stats)
            self.cache.remember(source_text, summary, stats)
            return summary

        self.tracker.record("llm1", prompt, summary)
        self.cache.remember(source_text, summary, stats)
        return summary
