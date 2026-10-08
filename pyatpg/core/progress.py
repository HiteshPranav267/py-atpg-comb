"""Live progress of a long-running analysis, for the web UI to poll -- and its cancellation.

Analysis code calls :func:`report` with a short human-readable stage
("Synthesizing RTL with Yosys", "Running Atalanta (non-targeted)"...). It is
a no-op unless the current request registered a tracker with :func:`track`,
so library and CLI use pay nothing. The tracker travels in a context
variable, which Starlette copies into the worker thread a request runs on.

A tracked run can be cancelled (:func:`cancel`, when its browser disconnects):
the next :func:`report` -- and every long loop that polls :func:`is_cancelled`
-- then stops it, so an abandoned run no longer holds the server's single
analysis slot until it finishes on its own.
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


class RunCancelled(Exception):
    """The run was cancelled because nobody is waiting for its result any more."""


def is_cancelled() -> bool:
    """Has the current request's run been cancelled?"""
    pid = _current.get()
    if pid is None:
        return False
    with _lock:
        entry = _entries.get(pid)
        return bool(entry and entry.get("cancelled"))


def check_cancelled() -> None:
    """Raise :class:`RunCancelled` if the current run has been cancelled."""
    if is_cancelled():
        raise RunCancelled("The analysis was cancelled because the browser disconnected.")


def cancel(progress_id: str) -> None:
    with _lock:
        entry = _entries.get(str(progress_id)[:_MAX_ID_LEN])
        if entry is not None:
            entry["cancelled"] = True


def active(exclude: str | None = None) -> list[dict[str, Any]]:
    """Runs still in progress (oldest first), without their ids."""
    now = time.monotonic()
    with _lock:
        rows = [(e["started"], e["stage"]) for pid, e in _entries.items()
                if not e["done"] and not e.get("cancelled") and pid != exclude]
    return [{"stage": stage, "elapsed_s": round(now - started, 1)} for started, stage in sorted(rows)]


def current_id() -> str | None:
    return _current.get()


def report(stage: str) -> None:
    """Record that the current request has moved on to ``stage``.

    Raises :class:`RunCancelled` if the run was cancelled, which makes every
    stage boundary a cancellation point.
    """
    pid = _current.get()
    if pid is None:
        return
    check_cancelled()
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
                         "history": [], "done": False, "cancelled": False}
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
