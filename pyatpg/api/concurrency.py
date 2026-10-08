"""Run blocking analysis work off the event loop, a bounded number at a time.

The upload routes are ``async def`` but called the (CPU-bound, minutes-long)
analysis services directly, which blocked the server's event loop for the
whole run: no other request -- not even ``/health`` -- was answered until it
finished, so the hosting platform's health check failed and the backend was
restarted mid-run ("the backend keeps going down"). :func:`run_heavy` moves
the work to a worker thread, keeping the server responsive, and caps how
many analyses run at once (``PYATPG_MAX_CONCURRENT_RUNS``, default 1) so two
large uploads cannot together exhaust a small instance's memory; extra
requests wait their turn instead.

With a single slot, a run whose browser has gone away (page refreshed, tab
closed, request timed out) must not keep it: :func:`run_tracked` watches for
the client disconnecting and cancels the run, which stops at its next
cancellation point (see :mod:`pyatpg.core.progress`).
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from pyatpg.core import progress, settings

T = TypeVar("T")


def slot_count() -> int:
    """Analyses allowed to run at once (live setting ``max_concurrent_runs``)."""
    return max(1, int(settings.get("max_concurrent_runs")))


# A counting slot pool rather than a fixed Semaphore, so the admin can change
# the slot count on a running server.
_slots_cond = threading.Condition()
_slots_in_use = 0


def _try_acquire(timeout: float) -> bool:
    global _slots_in_use
    with _slots_cond:
        if _slots_in_use >= slot_count():
            _slots_cond.wait(timeout)
        if _slots_in_use < slot_count():
            _slots_in_use += 1
            return True
        return False


def _release() -> None:
    global _slots_in_use
    with _slots_cond:
        _slots_in_use -= 1
        _slots_cond.notify_all()


def slots_changed() -> None:
    """Wake queued runs after the slot count was raised."""
    with _slots_cond:
        _slots_cond.notify_all()


def slots_in_use() -> int:
    return _slots_in_use


#: The most recent finished requests, newest last, for the admin page.
_history: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
_history_lock = threading.Lock()


def recent_runs() -> list[dict[str, Any]]:
    with _history_lock:
        return list(reversed(_history))


def clear_history() -> None:
    with _history_lock:
        _history.clear()


def _record(pid: str, label: str, started: float, started_at: str, outcome: str,
            detail: str = "") -> None:
    with _history_lock:
        _history.append({
            "id": pid, "label": label, "started_at": started_at,
            "duration_s": round(time.monotonic() - started, 1),
            "outcome": outcome, "detail": detail[:500],
        })


class MaintenanceMode(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            detail="The server is in maintenance mode and is not accepting new analyses "
                   "right now. Please try again shortly.",
        )


def _format_s(s: float) -> str:
    return f"{int(s)}s" if s < 60 else f"{int(s // 60)}m {int(s % 60):02d}s"


def _wait_for_slot() -> None:
    """Block until an analysis slot is free, reporting what is ahead in line."""
    shown = None
    while not _try_acquire(timeout=1.0):
        progress.check_cancelled()
        ahead = progress.active(exclude=progress.current_id())
        if ahead:
            run = ahead[0]
            stage = (f"Waiting for another analysis on this server to finish "
                     f"(it has run {_format_s(run['elapsed_s'])}; now: {run['stage']})")
        else:
            stage = "Waiting for another analysis on this server to finish"
        # Re-report only when what is ahead changes stage, so this step's
        # own timer keeps counting.
        key = ahead[0]["stage"] if ahead else None
        if key != shown:
            progress.report(stage)
            shown = key


async def run_heavy(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` on a worker thread, at most N concurrently."""

    def call() -> T:
        pid = progress.current_id()
        progress.set_slot_state(pid, "queued")
        try:
            _wait_for_slot()
        except BaseException:
            progress.set_slot_state(pid, None)
            raise
        progress.set_slot_state(pid, "running")
        try:
            return fn(*args, **kwargs)
        finally:
            progress.set_slot_state(pid, None)
            _release()

    return await run_in_threadpool(call)


async def run_tracked(request: Request, progress_id: str | None,
                      fn: Callable[..., T], *args: Any, label: str = "Analysis",
                      **kwargs: Any) -> T:
    """:func:`run_heavy` under a progress tracker, cancelled if the client leaves.

    ``label`` names the run in the admin page's recent-runs log.

    Raises:
        MaintenanceMode: the server is not accepting new analyses.
        progress.RunCancelled: the client disconnected (or the run was
            cancelled some other way) before it finished.
    """
    pid = progress_id or f"auto-{uuid.uuid4()}"
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if settings.get("maintenance"):
        _record(pid, label, started, started_at, "rejected", "maintenance mode")
        raise MaintenanceMode()
    with progress.track(pid):
        task = asyncio.ensure_future(run_heavy(fn, *args, **kwargs))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=1.0)
                if done:
                    result = task.result()
                    _record(pid, label, started, started_at, "ok")
                    return result
                if await request.is_disconnected():
                    progress.cancel(pid)
                    # Let the worker thread notice and wind down; its result
                    # (or RunCancelled) is discarded either way.
                    task.add_done_callback(lambda t: t.exception())
                    raise progress.RunCancelled(
                        "The analysis was cancelled because the browser disconnected."
                    )
        except progress.RunCancelled as exc:
            _record(pid, label, started, started_at, "cancelled", str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            _record(pid, label, started, started_at, "error", str(exc) or type(exc).__name__)
            raise
