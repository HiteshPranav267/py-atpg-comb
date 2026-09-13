"""Retry-with-backoff for one unit of batch work (one circuit, one dataset).

The LLM providers already retry a single API call a few times (see
``LLMProvider._with_retry`` in ``pyatpg.ranking.providers``); this is one
level up, retrying the *whole* operation -- an Atalanta invocation plus
however many LLM calls it makes -- for failures that outlast that inner
retry budget (a sustained rate limit, a flaky network) or that come from
somewhere the inner retry can't see (Atalanta itself).

Not every failure deserves a retry: a malformed netlist will fail the same
way a hundred times. :func:`classify_error` tells those apart so a suite run
doesn't burn its retry budget -- or an unattended overnight run's time --
retrying something that can never succeed.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class Classification(str, Enum):
    #: Will succeed if tried again: a rate limit, a timeout, a dropped
    #: connection, a transient Atalanta failure.
    TRANSIENT = "transient"
    #: Will fail the same way every time: something about this specific
    #: circuit or request. Retrying wastes the budget other circuits need.
    PERMANENT = "permanent"
    #: Not about this circuit at all -- the engine itself is unusable (e.g.
    #: the Atalanta binary is missing). Retrying, or continuing to the next
    #: circuit, just repeats the same failure. The whole suite run should
    #: stop and surface this rather than grind through every remaining
    #: circuit failing one at a time.
    FATAL = "fatal"


def classify_error(exc: BaseException) -> Classification:
    """Decide whether ``exc`` is worth retrying.

    Import lazily: this module has no hard dependency on pyatpg.atpg, so
    the retry primitives stay usable (and testable) on their own.
    """
    try:
        from pyatpg.atpg.binary import AtalantaNotAvailable
    except ImportError:  # pragma: no cover - always available in this repo
        AtalantaNotAvailable = ()  # type: ignore[assignment]

    if isinstance(exc, AtalantaNotAvailable):
        return Classification.FATAL

    # ValueError covers both "this circuit is malformed" (bad netlist, a
    # combinational cycle Atalanta can't levelize, no targetable gates) and
    # a config mistake (e.g. an unknown --llm-provider name). Neither is
    # fixed by trying again.
    if isinstance(exc, ValueError):
        return Classification.PERMANENT

    # Everything else -- RuntimeError (LLM providers wrap every failure,
    # including an exhausted retry budget, as one of these), OSError,
    # TimeoutError, ConnectionError, AtalantaExecutionError (a RuntimeError
    # subclass) -- is treated as worth retrying. A batch runner would rather
    # over-retry an unusual permanent failure a few times than give up on a
    # circuit that just hit a bad minute of API availability.
    return Classification.TRANSIENT


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with a cap and jitter.

    Defaults suit an unattended run: several minutes of retrying inside one
    invocation, with more persistence available across invocations -- a
    circuit that exhausts this policy is recorded as retryable-later, so
    the next scheduled run tries it again with a fresh budget rather than
    that circuit being lost.
    """

    max_attempts: int = 6
    base_delay: float = 15.0
    max_delay: float = 300.0
    #: Uniform jitter as a fraction of the computed delay, so many failures
    #: at once (e.g. a shared rate limit) don't all retry in lockstep.
    jitter: float = 0.2

    def delay_for(self, attempt: int) -> float:
        """Delay before the given attempt (1-indexed) is retried."""
        base = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        spread = base * self.jitter
        return max(0.0, base + random.uniform(-spread, spread))


@dataclass
class RetryOutcome:
    ok: bool
    attempts: int
    value: object = None
    error: str | None = None
    classification: Classification | None = None


def run_with_retries(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> RetryOutcome:
    """Call ``fn`` up to ``policy.max_attempts`` times.

    Stops immediately (no further attempts) on a
    :class:`~pyatpg.automation.retry.Classification.PERMANENT` or ``FATAL``
    error -- retrying either just burns time. ``on_attempt(n, error)`` is
    called after every attempt (``error`` is None on success), so a caller
    can log progress or persist state between attempts.
    """
    last_error: BaseException | None = None
    last_classification: Classification | None = None
    attempts_used = 0
    for attempt in range(1, policy.max_attempts + 1):
        attempts_used = attempt
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - classified immediately below
            last_error = exc
            last_classification = classify_error(exc)
            if on_attempt is not None:
                on_attempt(attempt, exc)
            if last_classification is not Classification.TRANSIENT or attempt == policy.max_attempts:
                break
            sleep(policy.delay_for(attempt))
            continue
        else:
            if on_attempt is not None:
                on_attempt(attempt, None)
            return RetryOutcome(ok=True, attempts=attempt, value=value)

    return RetryOutcome(
        ok=False,
        attempts=attempts_used,
        error=str(last_error) if last_error else None,
        classification=last_classification,
    )
