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

from pyatpg.core import progress, run_db, settings

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


async def run_heavy(fn: Callable[..., T], *args: Any,
                    _on_start: Callable[[], None] | None = None, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` on a worker thread, at most N concurrently.

    ``_on_start`` is called once the run has its slot (end of queueing).
    """

    def call() -> T:
        pid = progress.current_id()
        progress.set_slot_state(pid, "queued")
        try:
            _wait_for_slot()
        except BaseException:
            progress.set_slot_state(pid, None)
            raise
        progress.set_slot_state(pid, "running")
        if _on_start is not None:
            _on_start()
        try:
            return fn(*args, **kwargs)
        finally:
            progress.set_slot_state(pid, None)
            _release()

    return await run_in_threadpool(call)


async def run_tracked(request: Request, progress_id: str | None,
                      fn: Callable[..., T], *args: Any, label: str = "Analysis",
                      db_run_type: str | None = None, db_meta: dict[str, Any] | None = None,
                      db_describe: Callable[[T], dict] | None = None,
                      **kwargs: Any) -> T:
    """:func:`run_heavy` under a progress tracker, cancelled if the client leaves.

    ``label`` names the run in the admin page's recent-runs log. With
    ``db_run_type`` the run is also recorded in the run database
    (:mod:`pyatpg.core.run_db`): ``db_meta`` holds request-level columns
    (upload classification, LLM choice, parameters) and ``db_describe``
    turns the result into per-engine rows, off the request path.

    Raises:
        MaintenanceMode: the server is not accepting new analyses.
        progress.RunCancelled: the client disconnected (or the run was
            cancelled some other way) before it finished.
    """
    pid = progress_id or f"auto-{uuid.uuid4()}"
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = None
    if db_run_type and run_db.enabled():
        record = run_db.base_record(run_type=db_run_type, endpoint=request.url.path, label=label,
                                    progress_id=progress_id or None, request=request, meta=db_meta)
    slot_time: dict[str, datetime] = {}

    def on_start() -> None:
        slot_time["started"] = datetime.now(timezone.utc)

    def finish(outcome: str, status: int, error: BaseException | None = None,
               cancelled_by: str | None = None, result: Any = None) -> None:
        if record is None:
            return
        snap = progress.snapshot(pid) or {}
        run_db.finish_record(record, started_at=slot_time.get("started"), outcome=outcome,
                             http_status=status, error=error, error_stage=snap.get("stage"),
                             cancelled_by=cancelled_by, result=result, describe=db_describe)

    if settings.get("maintenance"):
        _record(pid, label, started, started_at, "rejected", "maintenance mode")
        exc = MaintenanceMode()
        finish("rejected", 503, exc)
        raise exc
    disconnected = False
    with progress.track(pid):
        task = asyncio.ensure_future(run_heavy(fn, *args, _on_start=on_start, **kwargs))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=1.0)
                if done:
                    result = task.result()
                    _record(pid, label, started, started_at, "ok")
                    finish("ok", 200, result=result)
                    return result
                if await request.is_disconnected():
                    progress.cancel(pid)
                    # Let the worker thread notice and wind down; its result
                    # (or RunCancelled) is discarded either way.
                    task.add_done_callback(lambda t: t.exception())
                    disconnected = True
                    raise progress.RunCancelled(
                        "The analysis was cancelled because the browser disconnected."
                    )
        except progress.RunCancelled as exc:
            _record(pid, label, started, started_at, "cancelled", str(exc))
            finish("cancelled", 499, exc,
                   cancelled_by="client_disconnect" if disconnected else "admin")
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            _record(pid, label, started, started_at, "error", str(exc) or type(exc).__name__)
            outcome, status, _ = run_db.classify_error(exc)
            finish(outcome, status, exc)
            raise
