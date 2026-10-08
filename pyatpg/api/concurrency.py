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
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, TypeVar

from starlette.concurrency import run_in_threadpool

T = TypeVar("T")


def _slots() -> int:
    try:
        return max(1, int(os.environ.get("PYATPG_MAX_CONCURRENT_RUNS", "1")))
    except ValueError:
        return 1


_SEMAPHORE = threading.BoundedSemaphore(_slots())


async def run_heavy(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` on a worker thread, at most N concurrently."""
    from pyatpg.core import progress

    def call() -> T:
        if not _SEMAPHORE.acquire(blocking=False):
            progress.report("Waiting for another analysis on this server to finish")
            _SEMAPHORE.acquire()
        try:
            return fn(*args, **kwargs)
        finally:
            _SEMAPHORE.release()

    return await run_in_threadpool(call)
