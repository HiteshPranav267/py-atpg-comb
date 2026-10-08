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


def cancel(progress_id: str) -> bool:
    """Cancel one run; False if no such run is known."""
    with _lock:
        entry = _entries.get(str(progress_id)[:_MAX_ID_LEN])
        if entry is None:
            return False
        entry["cancelled"] = True
        return True


def active(exclude: str | None = None) -> list[dict[str, Any]]:
    """Runs currently holding an analysis slot (oldest first), without their ids."""
    now = time.monotonic()
    with _lock:
        rows = [(e["started"], e["stage"]) for pid, e in _entries.items()
                if e.get("slot") == "running" and pid != exclude]
    return [{"stage": stage, "elapsed_s": round(now - started, 1)} for started, stage in sorted(rows)]


def current_id() -> str | None:
    return _current.get()


def set_slot_state(progress_id: str | None, state: str | None) -> None:
    """Record whether a run is "queued" for, "running" in, or out of (None) a slot.

    Set by :mod:`pyatpg.api.concurrency`. A run in a slot is kept even after
    its request has returned (e.g. its browser left before it noticed the
    cancellation), so the admin view shows everything actually holding the
    server, and it is never pruned while it does.
    """
    if not progress_id:
        return
    with _lock:
        entry = _entries.get(progress_id)
        if entry is not None:
            entry["slot"] = state


def list_runs() -> list[dict[str, Any]]:
    """Every run that is queued or holding an analysis slot, oldest first."""
    now = time.monotonic()
    with _lock:
        rows = [
            {
                "id": pid,
                "state": e["slot"],
                "stage": e["stage"],
                "elapsed_s": round(now - e["started"], 1),
                "stage_elapsed_s": round(now - e["stage_started"], 1),
                "cancelled": bool(e.get("cancelled")),
                "client_connected": not e["done"],
                "_started": e["started"],
            }
            for pid, e in _entries.items() if e.get("slot")
        ]
    rows.sort(key=lambda r: r.pop("_started"))
    return rows


def cancel_all() -> int:
    """Cancel every queued or running run; return how many were cancelled."""
    with _lock:
        hit = [e for e in _entries.values() if e.get("slot") and not e.get("cancelled")]
        for e in hit:
            e["cancelled"] = True
    return len(hit)


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
        entry["thought"] = None  # a new step starts with no model reasoning shown
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
            "thought": entry.get("thought"),
        }


#: How much of the model's reasoning the UI is given: about two lines.
_THOUGHT_CHARS = 220


def think(reasoning: str) -> None:
    """Show the tail of what the model is reasoning about right now.

    Called repeatedly while a reasoning model streams its answer; only the
    last ~two lines are kept, starting at a word boundary, so the UI shows
    what it is thinking now rather than the whole trace. Cleared by the
    next :func:`report`. A no-op outside a tracked request.
    """
    pid = _current.get()
    if pid is None:
        return
    text = " ".join((reasoning or "").split())
    if len(text) > _THOUGHT_CHARS:
        text = text[-_THOUGHT_CHARS:]
        cut = text.find(" ")
        text = "…" + (text[cut + 1:] if 0 <= cut < 40 else text)
    with _lock:
        entry = _entries.get(pid)
        if entry is not None:
            entry["thought"] = text or None


def _prune(now: float) -> None:
    for pid in [p for p, e in _entries.items()
                if e["done"] and not e.get("slot") and now - e.get("finished", now) > _KEEP_S]:
        del _entries[pid]
