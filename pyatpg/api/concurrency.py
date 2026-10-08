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
import os
import threading
import uuid
from typing import Any, Callable, TypeVar

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from pyatpg.core import progress

T = TypeVar("T")


def slot_count() -> int:
    return _slots()


def _slots() -> int:
    try:
        return max(1, int(os.environ.get("PYATPG_MAX_CONCURRENT_RUNS", "1")))
    except ValueError:
        return 1


_SEMAPHORE = threading.BoundedSemaphore(_slots())


def _format_s(s: float) -> str:
    return f"{int(s)}s" if s < 60 else f"{int(s // 60)}m {int(s % 60):02d}s"


def _wait_for_slot() -> None:
    """Block until an analysis slot is free, reporting what is ahead in line."""
    shown = None
    while not _SEMAPHORE.acquire(timeout=1.0):
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
            _SEMAPHORE.release()

    return await run_in_threadpool(call)


async def run_tracked(request: Request, progress_id: str | None,
                      fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """:func:`run_heavy` under a progress tracker, cancelled if the client leaves.

    Raises:
        progress.RunCancelled: the client disconnected (or the run was
            cancelled some other way) before it finished.
    """
    pid = progress_id or f"auto-{uuid.uuid4()}"
    with progress.track(pid):
        task = asyncio.ensure_future(run_heavy(fn, *args, **kwargs))
        while True:
            done, _ = await asyncio.wait({task}, timeout=1.0)
            if done:
                return task.result()
            if await request.is_disconnected():
                progress.cancel(pid)
                # Let the worker thread notice and wind down; its result (or
                # RunCancelled) is discarded either way.
                task.add_done_callback(lambda t: t.exception())
                raise progress.RunCancelled(
                    "The analysis was cancelled because the browser disconnected."
                )
