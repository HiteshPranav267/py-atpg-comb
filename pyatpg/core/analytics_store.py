"""Lightweight, file-based product-usage analytics.

Not visitor/page-view analytics (that's Vercel's job, via
@vercel/analytics on the frontend) -- this tracks what the tool itself
did: circuits parsed, ATPG runs (combinational/sequential, coverage
achieved, LLM usage), ranking runs, Trojan dataset generations. Stored as
an append-only JSON log, same pattern as ``pyatpg.core.memory.CircuitMemory``,
so no database dependency is introduced for what is fundamentally a small
amount of data.
"""
from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Any

_DEFAULT_STORAGE_PATH = os.path.join(os.getcwd(), "outputs", "analytics_log.json")

#: Oldest events are dropped past this count, so the log file can't grow
#: unbounded on a long-lived deployment.
_MAX_EVENTS = 5000


class AnalyticsStore:
    """Append-only event log plus a few pre-aggregated summaries."""

    def __init__(self, storage_path: str | None = None):
        self.storage_path = storage_path or _DEFAULT_STORAGE_PATH
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self._lock = Lock()

    def _load(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.storage_path):
            return []
        try:
            with open(self.storage_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, events: list[dict[str, Any]]) -> None:
        with open(self.storage_path, "w", encoding="utf-8") as handle:
            json.dump(events, handle)

    def record(self, event_type: str, **details: Any) -> None:
        """Append one event. Never raises: analytics must not break a request."""
        try:
            with self._lock:
                events = self._load()
                events.append({"ts": time.time(), "type": event_type, **details})
                if len(events) > _MAX_EVENTS:
                    events = events[-_MAX_EVENTS:]
                self._save(events)
        except Exception:  # noqa: BLE001 - best-effort telemetry, not a critical path
            pass

    def summary(self, recent_limit: int = 25) -> dict[str, Any]:
        events = self._load()

        def count(event_type: str) -> int:
            return sum(1 for e in events if e.get("type") == event_type)

        atpg_events = [e for e in events if e.get("type") == "atpg_run"]
        combinational = [e for e in atpg_events if e.get("circuit_kind") == "combinational"]
        sequential = [e for e in atpg_events if e.get("circuit_kind") == "sequential"]

        def avg_coverage(rows: list[dict[str, Any]]) -> float | None:
            values = [r["fault_coverage"] for r in rows if r.get("fault_coverage") is not None]
            return round(sum(values) / len(values), 2) if values else None

        llm_calls = sum(e.get("llm_api_calls") or 0 for e in atpg_events)
        llm_tokens = sum(e.get("llm_tokens_est") or 0 for e in atpg_events)
        runs_with_llm = sum(1 for e in atpg_events if e.get("llm_api_calls"))

        coverage_over_time = [
            {
                "ts": e["ts"],
                "coverage": e.get("fault_coverage"),
                "circuit_kind": e.get("circuit_kind"),
                "circuit_name": e.get("circuit_name"),
            }
            for e in atpg_events
            if e.get("fault_coverage") is not None
        ]

        return {
            "total_events": len(events),
            "circuits_parsed": count("circuit_parsed"),
            "atpg_runs": {
                "total": len(atpg_events),
                "combinational": len(combinational),
                "sequential": len(sequential),
                "avg_coverage_combinational": avg_coverage(combinational),
                "avg_coverage_sequential": avg_coverage(sequential),
            },
            "llm": {
                "runs_with_llm": runs_with_llm,
                "total_api_calls": llm_calls,
                "total_tokens_est": llm_tokens,
            },
            "ranking_runs": count("ranking_run"),
            "trojan_generations": count("trojan_generated"),
            "benchmark_runs": count("benchmark_run"),
            "coverage_over_time": coverage_over_time[-100:],
            "recent_activity": list(reversed(events))[:recent_limit],
        }


_default_store: AnalyticsStore | None = None


def get_analytics_store() -> AnalyticsStore:
    """Process-wide default store, so routes don't each construct their own."""
    global _default_store
    if _default_store is None:
        _default_store = AnalyticsStore()
    return _default_store
