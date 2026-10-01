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
    # A request too large for the per-request cap (HTTP 413) also says
    # "rate_limit_exceeded" on Groq, but waiting never fixes it -- the caller
    # has to send less -- so it must not be retried here.
    if "413" in text or "too large" in text or "reduce your message size" in text:
        return False
    if "429" in text or "rate limit" in text or "rate-limit" in text or "quota" in text:
        return True
    # A dropped connection or a provider hiccup (5xx, timeout) also clears on
    # its own; over a multi-hour run one is almost certain, and it should not
    # end the run any more than a quota limit does.
    return is_transient_error(exc)


_TRANSIENT_MARKERS = ("connection error", "timed out", "timeout", "502", "503", "504",
                      "500", "internal server error", "service unavailable", "bad gateway",
                      "overloaded")


def is_transient_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def _retry_after_header(exc: BaseException) -> float | None:
    """A ``Retry-After`` header on the HTTP error, if the SDK kept one.

    Providers wrap the SDK error, so walk the cause chain to find it.
    Cerebras, unlike Groq, puts the wait there rather than in the message.
    """
    seen = 0
    while exc is not None and seen < 5:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers is not None:
            try:
                value = headers.get("retry-after")
                if value is not None:
                    return max(1.0, float(value))
            except (TypeError, ValueError):
                pass
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


def retry_after_seconds(exc: BaseException, default: float = 300.0) -> float:
    """Seconds the provider asked us to wait, from its 429 message or headers."""
    m = _RETRY_RE.search(str(exc))
    if not m or not any(m.groups()):
        header = _retry_after_header(exc)
        if header is not None:
            return header
        if "minute" in str(exc).lower():
            return 60.0  # a per-minute limit clears within a minute
        if is_transient_error(exc) and "429" not in str(exc) and "quota" not in str(exc).lower():
            return 45.0  # network/server hiccup: retry soon
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
            log(f"    LLM {'unavailable' if is_transient_error(exc) else 'quota reached'}; waiting {delay / 60:.1f} min (until ~{resume}) and retrying")
            time.sleep(delay)
            waited += delay
