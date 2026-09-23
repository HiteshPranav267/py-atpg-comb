"""Call/token accounting so context-reduction can be measured, not assumed.

Counts come from the provider's own usage report whenever the SDK returns
one (every supported provider does); a call is only *estimated* -- as
``len(text) // 4``, the usual rough English/code heuristic -- when the
provider reported nothing, and the summary says which of the two it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class CallRecord:
    stage: str  # e.g. "llm1", "llm2", "targeting", "analysis"
    prompt_chars: int
    response_chars: int
    prompt_tokens_est: int
    response_tokens_est: int
    cached: bool = False
    #: True when the token counts came from the provider's usage report
    #: rather than the character heuristic.
    exact: bool = False
    #: Wall-clock seconds the provider call itself took, measured by the
    #: caller around ``provider.complete(...)``. 0.0 for a cache hit -- the
    #: whole point of the cache is that no call happened.
    duration_s: float = 0.0


@dataclass
class TokenTracker:
    """Accumulates usage across every LLM call in one ATPG run."""

    calls: list[CallRecord] = field(default_factory=list)

    def record(
        self,
        stage: str,
        prompt: str,
        response: str,
        cached: bool = False,
        usage: dict | None = None,
        duration_s: float = 0.0,
    ) -> CallRecord:
        """Log one call. ``usage`` is the provider's report, when it gave one:
        a dict with ``prompt_tokens`` and ``completion_tokens``. ``duration_s``
        is wall-clock time the caller measured around the ``provider.complete``
        call -- this module has no way to measure it itself."""
        prompt_tokens = approx_tokens(prompt)
        response_tokens = approx_tokens(response)
        exact = False
        if usage:
            reported_in = usage.get("prompt_tokens")
            reported_out = usage.get("completion_tokens")
            if isinstance(reported_in, int) and isinstance(reported_out, int):
                prompt_tokens, response_tokens, exact = reported_in, reported_out, True
        rec = CallRecord(
            stage=stage,
            prompt_chars=len(prompt),
            response_chars=len(response),
            prompt_tokens_est=prompt_tokens,
            response_tokens_est=response_tokens,
            cached=cached,
            exact=exact,
            duration_s=duration_s,
        )
        self.calls.append(rec)
        return rec

    def record_cached(self, stage: str) -> CallRecord:
        rec = CallRecord(stage=stage, prompt_chars=0, response_chars=0,
                          prompt_tokens_est=0, response_tokens_est=0, cached=True)
        self.calls.append(rec)
        return rec

    @property
    def total_calls(self) -> int:
        return len([c for c in self.calls if not c.cached])

    @property
    def total_prompt_tokens(self) -> int:
        return sum(c.prompt_tokens_est for c in self.calls)

    @property
    def total_response_tokens(self) -> int:
        return sum(c.response_tokens_est for c in self.calls)

    @property
    def total_duration_s(self) -> float:
        """Wall-clock seconds actually spent waiting on the provider.

        Sums only measured call durations; a call recorded with no timer
        around it (duration_s left at its 0.0 default) silently contributes
        nothing rather than understating the total as if it were free -- the
        caller distinguishes the two by checking whether every call it made
        was measured.
        """
        return sum(c.duration_s for c in self.calls)

    @property
    def tokens_exact(self) -> bool:
        """Every counted call carried a provider usage report.

        Vacuously true when every call was a cache hit: there is nothing
        estimated about zero tokens.
        """
        return all(c.exact for c in self.calls if not c.cached)

    def summary(self) -> dict:
        return {
            "api_calls": self.total_calls,
            "cached_hits": len(self.calls) - self.total_calls,
            "prompt_tokens_est": self.total_prompt_tokens,
            "response_tokens_est": self.total_response_tokens,
            "total_tokens_est": self.total_prompt_tokens + self.total_response_tokens,
            "tokens_exact": self.tokens_exact,
            "total_duration_s": round(self.total_duration_s, 3),
            "by_stage": {
                stage: {
                    "calls": len([c for c in self.calls if c.stage == stage and not c.cached]),
                    "cached_hits": len([c for c in self.calls if c.stage == stage and c.cached]),
                    "prompt_tokens_est": sum(
                        c.prompt_tokens_est for c in self.calls if c.stage == stage
                    ),
                    "response_tokens_est": sum(
                        c.response_tokens_est for c in self.calls if c.stage == stage
                    ),
                    "tokens_est": sum(
                        c.prompt_tokens_est + c.response_tokens_est
                        for c in self.calls
                        if c.stage == stage
                    ),
                }
                for stage in sorted({c.stage for c in self.calls})
            },
        }
