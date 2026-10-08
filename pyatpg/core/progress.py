"""Live progress of a long-running analysis, for the web UI to poll.

Analysis code calls :func:`report` with a short human-readable stage
("Synthesizing RTL with Yosys", "Running Atalanta (non-targeted)"...). It is
a no-op unless the current request registered a tracker with :func:`track`,
so library and CLI use pay nothing. The tracker travels in a context
variable, which Starlette copies into the worker thread a request runs on.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_current: ContextVar[str | None] = ContextVar("pyatpg_progress_id", default=None)
_lock = threading.Lock()
_entries: dict[str, dict[str, Any]] = {}
#: Finished entries are kept this long so the UI's last poll still finds them.
_KEEP_S = 600.0
_MAX_ID_LEN = 64


def report(stage: str) -> None:
    """Record that the current request has moved on to ``stage``."""
    pid = _current.get()
    if pid is None:
        return
    now = time.monotonic()
    with _lock:
        entry = _entries.get(pid)
        if entry is None:
            return
        entry["stage"] = stage
        entry["stage_started"] = now
        entry["history"].append({"stage": stage, "at_s": round(now - entry["started"], 1)})
        del entry["history"][:-20]


@contextmanager
def track(progress_id: str | None) -> Iterator[None]:
    """Collect :func:`report` calls made inside this block under ``progress_id``."""
    if not progress_id:
        yield
        return
    pid = str(progress_id)[:_MAX_ID_LEN]
    now = time.monotonic()
    with _lock:
        _prune(now)
        _entries[pid] = {"stage": "Starting", "started": now, "stage_started": now,
                         "history": [], "done": False}
    token = _current.set(pid)
    try:
        yield
    finally:
        _current.reset(token)
        with _lock:
            if pid in _entries:
                _entries[pid]["done"] = True
                _entries[pid]["finished"] = time.monotonic()


def snapshot(progress_id: str) -> dict[str, Any] | None:
    """The current state of ``progress_id``, or None if unknown."""
    now = time.monotonic()
    with _lock:
        entry = _entries.get(str(progress_id)[:_MAX_ID_LEN])
        if entry is None:
            return None
        end = entry.get("finished", now)
        return {
            "stage": entry["stage"],
            "elapsed_s": round(end - entry["started"], 1),
            "stage_elapsed_s": round(end - entry["stage_started"], 1),
            "done": entry["done"],
            "history": list(entry["history"]),
        }


def _prune(now: float) -> None:
    for pid in [p for p, e in _entries.items() if e["done"] and now - e.get("finished", now) > _KEEP_S]:
        del _entries[pid]
