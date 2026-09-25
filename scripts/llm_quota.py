"""Wait out an LLM provider's rate/quota limit instead of giving up.

Groq's free tier caps tokens per day per organization and model, on a
rolling 24-hour window, and its 429 message says exactly how long until the
request fits ("Please try again in 34m0.336s"). An evaluation that needs
more calls than one window allows can therefore just sleep until then and
retry the same call, finishing unattended rather than losing the rest of
the run.
"""
from __future__ import annotations

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_RETRY_RE = re.compile(r"try again in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.I)


def is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate-limit" in text or "quota" in text


def retry_after_seconds(exc: BaseException, default: float = 300.0) -> float:
    """Seconds the provider asked us to wait, from its 429 message."""
    m = _RETRY_RE.search(str(exc))
    if not m or not any(m.groups()):
        return default
    hours, minutes, seconds = (float(g) if g else 0.0 for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def call_with_quota_wait(
    fn: Callable[[], T],
    max_total_wait_s: float,
    log: Callable[[str], None] = print,
) -> T:
    """Call ``fn``; on a quota error sleep as instructed and retry.

    Raises the last quota error once ``max_total_wait_s`` of waiting is used
    up, and any non-quota error immediately.
    """
    waited = 0.0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified below
            if not is_quota_error(exc):
                raise
            delay = retry_after_seconds(exc) + 15.0  # margin past the window edge
            if waited + delay > max_total_wait_s:
                raise
            resume = time.strftime("%H:%M", time.localtime(time.time() + delay))
            log(f"    LLM quota reached; waiting {delay / 60:.1f} min (until ~{resume}) and retrying")
            time.sleep(delay)
            waited += delay
