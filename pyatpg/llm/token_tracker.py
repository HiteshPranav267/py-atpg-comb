"""Call/token accounting so context-reduction can be measured, not assumed.

Token counts are approximated as ``len(text) // 4`` (a standard rough
English/code heuristic) rather than depending on a provider-specific
tokenizer -- good enough to compare "context sent" across strategies, which
is the only thing this is used for.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class CallRecord:
    stage: str  # "llm1" or "llm2"
    prompt_chars: int
    response_chars: int
    prompt_tokens_est: int
    response_tokens_est: int
    cached: bool = False


@dataclass
class TokenTracker:
    """Accumulates usage across every LLM call in one ATPG run."""

    calls: list[CallRecord] = field(default_factory=list)

    def record(self, stage: str, prompt: str, response: str, cached: bool = False) -> CallRecord:
        rec = CallRecord(
            stage=stage,
            prompt_chars=len(prompt),
            response_chars=len(response),
            prompt_tokens_est=approx_tokens(prompt),
            response_tokens_est=approx_tokens(response),
            cached=cached,
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

    def summary(self) -> dict:
        return {
            "api_calls": self.total_calls,
            "cached_hits": len(self.calls) - self.total_calls,
            "prompt_tokens_est": self.total_prompt_tokens,
            "response_tokens_est": self.total_response_tokens,
            "total_tokens_est": self.total_prompt_tokens + self.total_response_tokens,
            "by_stage": {
                stage: {
                    "calls": len([c for c in self.calls if c.stage == stage and not c.cached]),
                    "tokens_est": sum(
                        c.prompt_tokens_est + c.response_tokens_est
                        for c in self.calls
                        if c.stage == stage
                    ),
                }
                for stage in sorted({c.stage for c in self.calls})
            },
        }
