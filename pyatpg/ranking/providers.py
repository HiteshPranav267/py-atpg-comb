"""LLM provider abstraction.

Every place in the codebase that talks to a language model goes through
:class:`LLMProvider.complete`, so the provider (Gemini, OpenAI, Anthropic,
NVIDIA NIM, or any OpenAI-compatible endpoint such as Groq, Ollama or
OpenRouter) is a deployment/request-time choice rather than a code choice.

Configuration is resolved by :func:`resolve_llm_config` from, in order, the
request, an explicit value handed to the service, and the environment. When
no provider is named the key's prefix is used to guess one, so a user can
paste any vendor's key and have it "just work".
"""
from __future__ import annotations

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any

from dotenv import load_dotenv

# python-dotenv is a declared dependency specifically so a local .env can
# supply these credentials; every entry point (CLI, API server, tests)
# resolves keys through this module, so loading it once here covers all of
# them. Does not override variables already set in the
# environment.
load_dotenv()

# Transient failures (over capacity, rate limited, gateway hiccups) worth
# retrying. Matched as substrings against the exception text, which is the
# lowest common denominator across the SDKs.
def request_timeout_s() -> float:
    """Seconds one LLM request may take before it is abandoned.

    The SDKs default to 600 s (OpenAI, Anthropic) or no limit at all
    (google-genai), and OpenAI/Anthropic also retry internally; stacked under
    :meth:`LLMProvider._with_retry` and the fallback-key rotation, one hung
    provider kept an ATPG request waiting for over an hour. Override with
    ``PYATPG_LLM_TIMEOUT``.
    """
    from pyatpg.core import settings

    return max(1.0, float(settings.get("llm_timeout_s")))


_RETRYABLE_MARKERS = (
    "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL",
    "502", "504", "overloaded", "rate limit", "RateLimit", "Timeout", "timed out",
)

# "The account needs attention" -- out of credits / over quota, the key
# rejected, or no key at all -- across every provider's own wording for it.
# Matched case-insensitively. Deliberately heuristic: providers don't share
# an error taxonomy, so a message that slips past these is just described
# generically, which is safe rather than wrong. Deliberately *not* a bare
# "billing": Groq's per-request TPM error links to its billing/upgrade page
# in the same message, so that alone would misreport a request-too-large
# error as an exhausted account -- a different problem with a different fix.
_QUOTA_MARKERS = (
    "insufficient_quota", "exceeded your current quota", "quota exceeded",
    "credit balance",
)
# One request exceeded a per-request or per-minute size limit -- distinct
# from being rate-limited generally (retrying the identical request just
# fails the same way again) and from an exhausted account (no billing
# action fixes it; the request itself needs to be smaller).
_REQUEST_TOO_LARGE_MARKERS = (
    "reduce the length", "reduce your message size", "request too large",
    "context_length_exceeded", "maximum context length",
)
_AUTH_MARKERS = (
    "invalid api key", "invalid_api_key", "invalid x-api-key", "incorrect api key",
    "authentication_error", "permission_denied", "unauthorized", "401", "403",
)
_NO_KEY_MARKERS = (
    "no gemini api key available", "no openai api key available",
    "no anthropic api key available", "no nvidia api key available",
    "no llm api key available",
)
#: Retrying one of these is pointless until a human tops up billing or swaps
#: the key, so :meth:`LLMProvider._with_retry` fails fast on them rather than
#: treating them as a transient blip.
ACCOUNT_ERROR_MARKERS = _QUOTA_MARKERS + _AUTH_MARKERS + _NO_KEY_MARKERS


#: A rate-limit error that says how long to wait ("Please try again in
#: 12.855s" -- Groq, OpenAI). A per-minute token window never needs longer
#: than this to reopen.
_RETRY_AFTER = re.compile(r"try again in\s+(?:(\d+)m)?\s*([\d.]+)\s*(ms|s)\b", re.IGNORECASE)
_MAX_RETRY_WAIT_S = 65.0


def _retry_delay(error_text: str, attempt: int) -> float:
    """Seconds to wait before retrying: the provider's own hint, else 1s/2s/4s.

    Groq's free tier allows 8000 tokens per minute; two stages of one run
    can exceed that, and its 429 says exactly when the window reopens
    (often 10-15 s) -- longer than the fixed back-off, so every retry used
    to fail the same way.
    """
    m = _RETRY_AFTER.search(error_text or "")
    if m:
        minutes = int(m.group(1) or 0)
        value = float(m.group(2))
        seconds = minutes * 60 + (value / 1000 if m.group(3).lower() == "ms" else value)
        return min(_MAX_RETRY_WAIT_S, seconds + 1.0)
    return float(2 ** attempt)


class LLMUnavailableError(RuntimeError):
    """The model could not be run at all, so nothing it would have said exists.

    Raised by :meth:`LLMProvider._with_retry` once a provider call has
    failed for good -- out of credits, rate limited, the service returning
    a 5xx, the key rejected. This is deliberately *not* something the ATPG
    pipeline degrades around: a run the user asked to be LLM-assisted is
    aborted and told why, rather than silently substituting the structural
    heuristic and presenting that as the answer. The message is what the
    user sees; ``reason`` is the short human-readable cause and ``cause``
    the provider's original wording, kept so a caller can still classify
    it.
    """

    def __init__(self, provider_label: str, cause: str):
        self.provider_label = provider_label
        self.cause = cause
        self.reason = describe_llm_failure(cause)
        super().__init__(
            f"LLM could not be run: {self.reason}. "
            f"The ATPG run was stopped rather than falling back to the offline "
            f"structural heuristic. ({provider_label} failure: {cause})"
        )


def is_request_too_large(message: str) -> bool:
    """Whether a provider's error text means "this one request was too big".

    The remedy is a smaller request, not a retry and not a billing change,
    so callers that *can* resend something smaller (see
    :class:`~pyatpg.ranking.llm.LLMRanker`) branch on this.
    """
    text = message.lower()
    return any(m.lower() in text for m in _REQUEST_TOO_LARGE_MARKERS)


def describe_llm_failure(message: str) -> str:
    """Turn a provider's error text into one short plain-English cause.

    Account problems are checked before HTTP statuses because several
    vendors report an exhausted balance *as* a 429, and "you are out of
    credits" is the more useful of the two things to say about that.
    """
    text = message.lower()

    def has(*markers: str) -> bool:
        return any(m.lower() in text for m in markers)

    if has(*_NO_KEY_MARKERS):
        return "no API key was configured"
    if has(*_QUOTA_MARKERS):
        return "the LLM account is out of credits or over its quota"
    if has(*_AUTH_MARKERS):
        return "the LLM provider rejected the API key (authentication failed)"
    if has(*_REQUEST_TOO_LARGE_MARKERS):
        return (
            "the request was too large for the provider's per-request or "
            "per-minute token limit (not an account problem -- lower "
            "LLM_REQUEST_TOKEN_BUDGET to make every stage send less, or use "
            "a provider without so tight a per-request cap)"
        )
    if has("429", "resource_exhausted", "rate limit", "ratelimit"):
        return "the LLM provider rate-limited the request (HTTP 429)"
    if has("503", "unavailable", "overloaded"):
        return "the LLM provider is unavailable or overloaded (HTTP 503)"
    if has("500", "internal"):
        return "the LLM provider returned an internal server error (HTTP 500)"
    if has("502"):
        return "the LLM provider's gateway returned an error (HTTP 502)"
    if has("504"):
        return "the LLM provider's gateway timed out (HTTP 504)"
    if has("timeout", "timed out"):
        return "the request to the LLM provider timed out"
    return "the request to the LLM provider failed"


#: Generic environment variables honoured by every provider; the
#: provider-specific ones (``GEMINI_API_KEY`` etc.) still work too.
LLM_PROVIDER_ENV_VAR = "LLM_PROVIDER"
LLM_API_KEY_ENV_VAR = "LLM_API_KEY"
LLM_MODEL_ENV_VAR = "LLM_MODEL"
LLM_BASE_URL_ENV_VAR = "LLM_BASE_URL"

#: Provider id used when nothing else says otherwise and the key does not
#: look like any known vendor's: Groq, with openai/gpt-oss-120b.
DEFAULT_PROVIDER = "groq"

#: Sentinel a request may send to mean "work it out from the key".
AUTO_PROVIDER = "auto"


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one supported provider, for the UI and CLI."""

    id: str
    label: str
    #: Environment variable(s) checked for a credential, most specific first.
    env_vars: tuple[str, ...]
    default_model: str
    #: Prefix a key from this vendor usually starts with, for auto-detection
    #: and as an input placeholder. Empty when there is no stable prefix.
    key_prefix: str
    #: Whether the provider talks the OpenAI chat-completions protocol, and
    #: therefore accepts a ``base_url`` override.
    openai_compatible: bool = False
    #: Default endpoint for OpenAI-compatible providers; None means the SDK's.
    base_url: str | None = None
    #: Free-text shown next to the picker in the UI.
    hint: str = ""
    #: Curated model ids offered as dropdown choices, ``default_model`` first.
    #: Not exhaustive -- the UI always allows a custom id alongside these.
    models: tuple[str, ...] = ()
    #: Named endpoints offered as dropdown choices for a ``base_url`` field,
    #: as (label, url) pairs. Only meaningful when ``openai_compatible``.
    base_url_presets: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "env_vars": list(self.env_vars),
            "default_model": self.default_model,
            "key_prefix": self.key_prefix,
            "openai_compatible": self.openai_compatible,
            "base_url": self.base_url,
            "hint": self.hint,
            "models": list(self.models),
            "base_url_presets": [
                {"label": label, "url": url} for label, url in self.base_url_presets
            ],
        }


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        id="groq",
        label="Groq",
        env_vars=("GROQ_API_KEY",),
        default_model="openai/gpt-oss-120b",
        key_prefix="gsk_",
        openai_compatible=True,
        base_url="https://api.groq.com/openai/v1",
        hint=(
            "Keys from console.groq.com start with gsk_. Free tier available; each model "
            "has its own rate limits, so switching model is the way past a limit."
        ),
        models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
    ),
    "gemini": ProviderSpec(
        id="gemini",
        label="Google Gemini",
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        default_model="gemini-flash-latest",
        key_prefix="AIza",
        hint=(
            "Keys from aistudio.google.com start with AIza. Each model has its own "
            "free-tier quota, so switching model is the way past a daily limit."
        ),
        # The -latest aliases track Google's current release of each tier;
        # the pinned ids are the stable 2.5 family. Everything else the key
        # can use (newer previews, Gemma) comes from list_models(), which
        # returns the exact ids -- the AI Studio dashboard shows display
        # names, not ids, so they are deliberately not guessed here.
        models=(
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-pro-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.5-pro",
        ),
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        env_vars=("OPENAI_API_KEY",),
        default_model="gpt-4o-mini",
        key_prefix="sk-",
        openai_compatible=True,
        hint="Keys from platform.openai.com start with sk-.",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "o4-mini"),
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic Claude",
        env_vars=("ANTHROPIC_API_KEY",),
        default_model="claude-sonnet-5",
        key_prefix="sk-ant-",
        hint="Keys from console.anthropic.com start with sk-ant-.",
        models=("claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"),
    ),
    "nvidia": ProviderSpec(
        id="nvidia",
        label="NVIDIA NIM",
        env_vars=("NVIDIA_API_KEY",),
        default_model="meta/llama-3.3-70b-instruct",
        key_prefix="nvapi-",
        openai_compatible=True,
        base_url="https://integrate.api.nvidia.com/v1",
        hint="Keys from build.nvidia.com start with nvapi-. Any NIM model id works.",
        models=(
            "meta/llama-3.3-70b-instruct",
            "meta/llama-3.1-405b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "mistralai/mixtral-8x22b-instruct-v0.1",
            "deepseek-ai/deepseek-r1",
        ),
    ),
    "openai_compatible": ProviderSpec(
        id="openai_compatible",
        label="Custom (OpenAI-compatible)",
        env_vars=("OPENAI_COMPATIBLE_API_KEY",),
        default_model="",
        key_prefix="",
        openai_compatible=True,
        hint="Cerebras, OpenRouter, Together, Ollama, vLLM, LM Studio... anything "
             "exposing /v1/chat/completions. Set the base URL and model.",
        # No fixed model list -- every backend names models differently --
        # but the endpoints below are common enough to save typing.
        base_url_presets=(
            ("Ollama (local)", "http://localhost:11434/v1"),
            ("LM Studio (local)", "http://localhost:1234/v1"),
            ("Cerebras", "https://api.cerebras.ai/v1"),
            ("OpenRouter", "https://openrouter.ai/api/v1"),
            ("Together AI", "https://api.together.xyz/v1"),
        ),
    ),
}

#: Order the UI should list providers in.
PROVIDER_ORDER = ("groq", "gemini", "openai", "anthropic", "nvidia", "openai_compatible")


def list_provider_specs() -> list[dict[str, Any]]:
    return [PROVIDER_SPECS[pid].to_dict() for pid in PROVIDER_ORDER]


def detect_provider_from_key(api_key: str | None) -> str | None:
    """Guess the vendor from a key's prefix, or None when it is ambiguous.

    Checks the most specific prefixes first: ``sk-ant-`` must win over
    ``sk-`` or every Anthropic key would be mistaken for an OpenAI one.
    """
    key = (api_key or "").strip()
    if not key:
        return None
    candidates = sorted(
        (spec for spec in PROVIDER_SPECS.values() if spec.key_prefix),
        key=lambda s: -len(s.key_prefix),
    )
    for spec in candidates:
        if key.startswith(spec.key_prefix):
            return spec.id
    return None


def _is_groq_endpoint(base_url: str | None, key: str | None) -> bool:
    """A custom-endpoint request that is really Groq (its URL, or a Groq key and no URL)."""
    if base_url:
        return "api.groq.com" in base_url.lower()
    return detect_provider_from_key(key) == "groq"


def normalize_provider_id(raw: str | None) -> str | None:
    """Map user-facing spellings onto a provider id; None for auto/blank."""
    value = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not value or value == AUTO_PROVIDER:
        return None
    aliases = {
        "google": "gemini",
        "google_gemini": "gemini",
        "claude": "anthropic",
        "anthropic_claude": "anthropic",
        "gpt": "openai",
        "chatgpt": "openai",
        "nim": "nvidia",
        "nvidia_nim": "nvidia",
        "custom": "openai_compatible",
        "compatible": "openai_compatible",
        "openai_compat": "openai_compatible",
        "ollama": "openai_compatible",
        "openrouter": "openai_compatible",
    }
    value = aliases.get(value, value)
    if value not in PROVIDER_SPECS:
        raise ValueError(
            f"Unknown LLM provider {raw!r}. Expected one of: "
            + ", ".join(PROVIDER_ORDER)
            + f" (or '{AUTO_PROVIDER}')."
        )
    return value


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to build a provider for one request."""

    provider: str
    api_key: str
    model: str
    base_url: str | None = None
    #: Backup credentials, tried in order when this one is rate limited,
    #: out of quota or otherwise failing (see :class:`FallbackProvider`).
    fallbacks: tuple["LLMConfig", ...] = ()

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.provider]

    @property
    def api_keys(self) -> list[str]:
        """This key and every backup key, for redacting them from output."""
        return [self.api_key] + [f.api_key for f in self.fallbacks]

    def describe(self) -> str:
        """Human-readable, credential-free summary for notes and reports."""
        n = len(self.fallbacks)
        extra = f" (+{n} backup key{'s' if n > 1 else ''})" if n else ""
        return f"{self.spec.label} / {self.model}{extra}"


#: Most credentials one request may rotate through: the main key plus four backups.
MAX_LLM_KEYS = 5


def _first_set(*values: str | None) -> str | None:
    for value in values:
        if value and value.strip():
            return value.strip()
    return None


def _env_key_for(provider: str) -> str | None:
    for var in PROVIDER_SPECS[provider].env_vars:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


def resolve_llm_config(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    fallback_api_key: str | None = None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    fallback_base_url: str | None = None,
    fallbacks: list[dict[str, Any]] | None = None,
    require_model: bool = True,
) -> LLMConfig | None:
    """Work out which provider/key/model to use for a request.

    ``require_model=False`` lets a custom endpoint resolve without a model
    name, for asking it which models it has.

    ``fallbacks`` are backup credentials sent with the request, each a dict
    with ``api_key`` and optionally ``provider``/``model``/``base_url``,
    resolved independently. When the main key instead comes from the
    environment, numbered variables -- the key's own variable name plus
    ``_2`` .. ``_5`` -- supply backups for the same provider, model and
    endpoint. Either way at most :data:`MAX_LLM_KEYS` keys are used in
    total, and a backup identical to an earlier key is dropped.

    Precedence for every field is request value, then the explicit value the
    service was constructed with, then the environment. Returns None when no
    credential can be found anywhere, which callers treat as "run without
    the LLM".

    Provider resolution, when the request/service names none: a key that
    came with the request is sniffed by prefix, then ``LLM_PROVIDER`` from
    the environment, then :data:`DEFAULT_PROVIDER`. That order matters: a
    server pinned to one vendor by ``LLM_PROVIDER`` must not forward a
    user's pasted key for a different vendor to it. When the only key
    available is a provider-specific environment variable, that variable's
    provider wins, so an ``OPENAI_API_KEY``-only deployment works without
    also setting ``LLM_PROVIDER``.
    """
    requested_provider = normalize_provider_id(_first_set(provider, fallback_provider))
    env_provider = normalize_provider_id(os.environ.get(LLM_PROVIDER_ENV_VAR))
    key = _first_set(api_key, fallback_api_key)

    key_env_var: str | None = None  # which variable the key came from, if any
    if key is not None:
        resolved_provider = (
            requested_provider
            or detect_provider_from_key(key)
            or env_provider
            or DEFAULT_PROVIDER
        )
    else:
        # Environment fallback. Generic variable first, then whichever
        # provider-specific variable is set (preferring the requested one).
        key = _first_set(os.environ.get(LLM_API_KEY_ENV_VAR))
        if key is not None:
            key_env_var = LLM_API_KEY_ENV_VAR
        resolved_provider = requested_provider or env_provider
        if key is None:
            search = [resolved_provider] if resolved_provider else []
            search += [pid for pid in PROVIDER_ORDER if pid not in search]
            for pid in search:
                env_key = _env_key_for(pid)
                if env_key:
                    key = env_key
                    key_env_var = next(
                        v for v in PROVIDER_SPECS[pid].env_vars
                        if (os.environ.get(v) or "").strip() == env_key
                    )
                    if resolved_provider is None:
                        resolved_provider = pid
                    break
        if key is None:
            return None
        resolved_provider = (
            resolved_provider or detect_provider_from_key(key) or DEFAULT_PROVIDER
        )

    if resolved_provider == "openai_compatible" and _is_groq_endpoint(
        _first_set(base_url, fallback_base_url, os.environ.get(LLM_BASE_URL_ENV_VAR)), key
    ):
        # Groq used to be reachable only as a custom endpoint; browsers that
        # saved it that way get the real Groq provider (and its default model).
        resolved_provider = "groq"
        base_url = fallback_base_url = None

    spec = PROVIDER_SPECS[resolved_provider]

    resolved_model = _first_set(
        model, fallback_model, os.environ.get(LLM_MODEL_ENV_VAR), spec.default_model
    )
    if not resolved_model and not require_model:
        resolved_model = ""
    elif not resolved_model:
        raise ValueError(
            f"{spec.label} needs a model name: none was supplied with the request "
            f"and {LLM_MODEL_ENV_VAR} is not set."
        )

    resolved_base_url = None
    if spec.openai_compatible:
        resolved_base_url = _first_set(
            base_url, fallback_base_url, os.environ.get(LLM_BASE_URL_ENV_VAR), spec.base_url
        )
        if resolved_provider == "openai_compatible" and not resolved_base_url:
            raise ValueError(
                "A custom OpenAI-compatible provider needs a base URL: none was "
                f"supplied with the request and {LLM_BASE_URL_ENV_VAR} is not set."
            )

    config = LLMConfig(
        provider=resolved_provider,
        api_key=key,
        model=resolved_model,
        base_url=resolved_base_url,
    )

    backups: list[LLMConfig] = []
    for i, item in enumerate(fallbacks or [], start=2):
        item = item or {}
        backup_key = str(item.get("api_key") or "").strip()
        if not backup_key:
            continue  # an empty slot in the UI
        try:
            backup = resolve_llm_config(
                provider=(item.get("provider") or None),
                api_key=backup_key,
                model=(item.get("model") or None),
                base_url=(item.get("base_url") or None),
            )
        except ValueError as exc:
            raise ValueError(f"LLM key {i}: {exc}") from exc
        if backup is not None:
            backups.append(backup)
    if key_env_var is not None:
        for n in range(2, MAX_LLM_KEYS + 1):
            extra = _first_set(os.environ.get(f"{key_env_var}_{n}"))
            if extra:
                backups.append(replace(config, api_key=extra))

    seen = {config.api_key}
    unique = []
    for b in backups:
        if b.api_key not in seen:
            seen.add(b.api_key)
            unique.append(b)
    return replace(config, fallbacks=tuple(unique[: MAX_LLM_KEYS - 1])) if unique else config


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    #: Model identifier, surfaced in reports so a result can be attributed.
    model_name: str = ""

    #: Token usage the provider reported for the most recent successful
    #: ``complete`` call, as ``{"prompt_tokens": int, "completion_tokens":
    #: int}``, or None when the SDK returned no usage block. Read it
    #: immediately after the call; the next call overwrites it.
    last_usage: dict[str, int] | None = None

    @abstractmethod
    def complete(
        self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None
    ) -> str:
        """Send the prompt and return the raw text response.

        ``json_mode`` asks the backend for a JSON object response where the
        provider supports it; callers must still validate the result, since
        not every provider can enforce it.

        ``max_output_tokens`` bounds the completion. Left unset, some
        endpoints reserve a large default against a per-minute token budget
        even though the model never gets close to using it -- Groq's free
        tier rejects a request outright once prompt-plus-reservation crosses
        its TPM limit, before the model runs at all. A caller that knows
        roughly how long its answer should be should pass a bound.
        """
        ...

    def list_models(self) -> list[dict[str, str]]:
        """Models this credential can use for text generation.

        Returns ``[{"id": ..., "label": ...}]`` in the vendor's own order.
        Raises :class:`LLMUnavailableError` when the vendor cannot be asked
        (bad key, quota, outage) -- the same failure the ATPG run would hit.
        Providers that cannot tell text models from the rest return every
        model and leave the choice to the user.
        """
        raise NotImplementedError

    def _note_usage(self, prompt_tokens: Any, completion_tokens: Any) -> None:
        """Record a usage report, ignoring anything that is not two ints."""
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            self.last_usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        else:
            self.last_usage = None

    @property
    def label(self) -> str:
        return self.__class__.__name__

    def _with_retry(self, call, max_retries: int = 3) -> str:
        """Run ``call`` with exponential back-off on transient failures.

        Whatever the call ultimately fails with is raised as
        :class:`LLMUnavailableError` -- the one signal the ATPG pipeline
        treats as "stop, the model could not be consulted". An account
        problem (no credits, bad key) is not retried at all: it cannot fix
        itself in the next four seconds.
        """
        last_error: Exception = RuntimeError(f"{self.label} failure")
        for attempt in range(max_retries):
            try:
                return call()
            except Exception as e:  # noqa: BLE001 - classified below
                if type(e).__name__ == "RunCancelled":
                    raise  # the user left: stop, do not report an LLM failure
                last_error = e
                text = str(e)
                account_problem = any(m in text.lower() for m in ACCOUNT_ERROR_MARKERS)
                retryable = not account_problem and any(m in text for m in _RETRYABLE_MARKERS)
                if not retryable or attempt == max_retries - 1:
                    raise LLMUnavailableError(self.label, text) from e
                time.sleep(_retry_delay(text, attempt))
        raise LLMUnavailableError(self.label, str(last_error)) from last_error


class GeminiProvider(LLMProvider):
    """Google Gemini via the ``google-genai`` SDK."""

    def __init__(self, model_name: str = "gemini-flash-latest", api_key: str | None = None):
        self.model_name = model_name
        self._client = None
        #: Caller-supplied credential, used ahead of the environment so a key
        #: can be changed per request without restarting the service.
        self._api_key = (api_key or "").strip() or None

    @property
    def label(self) -> str:
        return "Gemini"

    def _get_client(self):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Please install google-genai to use GeminiProvider")

        if not self._client:
            api_key = self._api_key or _env_key_for("gemini")
            if not api_key:
                raise ValueError(
                    "No Gemini API key available: none was supplied with the request "
                    "and GEMINI_API_KEY is not set."
                )
            from google.genai import types

            self._client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(request_timeout_s() * 1000)),
            )
        return self._client

    def list_models(self) -> list[dict[str, str]]:
        client = self._get_client()

        def call() -> list[dict[str, str]]:
            found: list[dict[str, str]] = []
            for model in client.models.list():
                # The SDK names the capability list differently across
                # versions; either way only generateContent models can run
                # this project's prompts (embeddings, TTS, image and video
                # models are listed under the same key).
                actions = (
                    getattr(model, "supported_actions", None)
                    or getattr(model, "supported_generation_methods", None)
                    or []
                )
                if actions and "generateContent" not in actions:
                    continue
                name = str(getattr(model, "name", "") or "")
                model_id = name.split("/", 1)[1] if name.startswith("models/") else name
                if not model_id:
                    continue
                found.append(
                    {"id": model_id, "label": str(getattr(model, "display_name", "") or model_id)}
                )
            return found

        return self._with_retry(call)

    def complete(
        self,
        prompt: str,
        *,
        json_mode: bool = False,
        max_output_tokens: int | None = None,
        max_retries: int = 3,
    ) -> str:
        client = self._get_client()
        config = None
        if json_mode or max_output_tokens:
            from google.genai import types as genai_types

            config = genai_types.GenerateContentConfig(
                response_mime_type="application/json" if json_mode else None,
                max_output_tokens=max_output_tokens,
            )

        def call() -> str:
            response = client.models.generate_content(
                model=self.model_name, contents=prompt, config=config
            )
            meta = getattr(response, "usage_metadata", None)
            self._note_usage(
                getattr(meta, "prompt_token_count", None),
                getattr(meta, "candidates_token_count", None),
            )
            return response.text or ""

        return self._with_retry(call, max_retries)


class OpenAIProvider(LLMProvider):
    """OpenAI, or any endpoint speaking its chat-completions protocol.

    NVIDIA NIM, Groq, OpenRouter, Together, Ollama, vLLM and LM Studio all
    expose ``/v1/chat/completions``; point ``base_url`` at them.
    """

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        label: str = "OpenAI",
    ):
        self.model_name = model_name
        self._api_key = (api_key or "").strip() or None
        self._base_url = (base_url or "").strip() or None
        self._label = label
        self._client = None

    @property
    def label(self) -> str:
        return self._label

    def _get_client(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Please install openai to use OpenAIProvider")

        if not self._client:
            api_key = self._api_key or _env_key_for("openai")
            if not api_key:
                raise ValueError(
                    f"No {self._label} API key available: none was supplied with the "
                    "request and OPENAI_API_KEY is not set."
                )
            # Retries are _with_retry's job (and the fallback keys'); the SDK's
            # own would multiply every wait.
            self._client = OpenAI(api_key=api_key, base_url=self._base_url,
                                  timeout=request_timeout_s(), max_retries=0)
        return self._client

    #: Model-id fragments that mark a non-chat model on OpenAI's list. The
    #: endpoint does not say what a model is for, so this is a courtesy
    #: filter, not a guarantee; compatible servers are left unfiltered.
    _NON_CHAT_MARKERS = (
        "embedding", "whisper", "tts", "dall-e", "moderation", "realtime",
        "audio", "transcribe", "image", "davinci", "babbage",
    )

    def list_models(self) -> list[dict[str, str]]:
        client = self._get_client()

        def call() -> list[dict[str, str]]:
            found: list[dict[str, str]] = []
            for model in client.models.list():
                model_id = str(getattr(model, "id", "") or "")
                if not model_id:
                    continue
                if self._base_url is None and any(
                    marker in model_id.lower() for marker in self._NON_CHAT_MARKERS
                ):
                    continue
                found.append({"id": model_id, "label": model_id})
            found.sort(key=lambda m: m["id"])
            return found

        return self._with_retry(call)

    def complete(
        self,
        prompt: str,
        *,
        json_mode: bool = False,
        max_output_tokens: int | None = None,
        max_retries: int = 3,
    ) -> str:
        client = self._get_client()
        # Only the real OpenAI endpoint is guaranteed to honour
        # response_format; compatible servers frequently reject it, and the
        # prompts already spell out the expected JSON shape.
        kwargs: dict[str, Any] = {}
        if json_mode and self._base_url is None:
            kwargs["response_format"] = {"type": "json_object"}
        if max_output_tokens:
            # Several free tiers (Groq's, notably) rate-limit on tokens-per-
            # minute and, with no cap given, reserve a large default against
            # that budget before the model even runs -- rejecting an
            # otherwise-small prompt outright. Bounding it here is what
            # keeps a capped-gate ranking prompt under that ceiling.
            kwargs["max_tokens"] = max_output_tokens
            if "gpt-oss" in self.model_name.lower():
                # gpt-oss reasons before answering, and by default spends
                # tokens on that hidden trace as freely as on the answer --
                # against a tight max_tokens it can exhaust the whole budget
                # mid-thought and never emit the JSON. Only this family is
                # known to accept the knob; other OpenAI-compatible backends
                # (Ollama included) 400 on it for non-reasoning models.
                kwargs["reasoning_effort"] = "low"

        from pyatpg.core import progress

        if progress.current_id() is not None:
            # A tracked web request: stream, so the model's reasoning can be
            # shown live (progress.think) and a cancelled run stops at once.
            return self._with_retry(lambda: self._stream(client, prompt, kwargs), max_retries)

        def call() -> str:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            usage = getattr(response, "usage", None)
            self._note_usage(
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
            )
            choice = response.choices[0] if response.choices else None
            return (choice.message.content if choice and choice.message else "") or ""

        return self._with_retry(call, max_retries)


    #: How often the live reasoning shown in the UI is refreshed.
    _THINK_EVERY_S = 0.3

    def _stream(self, client, prompt: str, kwargs: dict[str, Any]) -> str:
        """One streamed completion, publishing the model's reasoning as it arrives.

        Reasoning models served over the OpenAI API send their trace in a
        delta field beside ``content`` -- ``reasoning`` (Groq's gpt-oss,
        OpenRouter) or ``reasoning_content`` (DeepSeek, vLLM). The SDK's
        per-read timeout no longer bounds the whole call once tokens flow,
        so a total deadline is enforced here as well.
        """
        from pyatpg.core import progress

        deadline = time.monotonic() + 3 * request_timeout_s()
        stream = client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            **kwargs,
        )
        content: list[str] = []
        reasoning: list[str] = []
        usage = None
        last_shown = 0.0
        seen_chars = 0
        try:
            for chunk in stream:
                if progress.is_cancelled():
                    progress.check_cancelled()
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{self.label} request timed out while streaming")
                usage = getattr(chunk, "usage", None) or _groq_usage(chunk) or usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    content.append(delta.content)
                piece = _reasoning_piece(delta)
                if piece:
                    reasoning.append(piece)
                    seen_chars += len(piece)
                    now = time.monotonic()
                    # Wait for about half a line, so a step never shows a
                    # lone first word ("Need", "We").
                    if seen_chars >= 40 and now - last_shown >= self._THINK_EVERY_S:
                        progress.think("".join(reasoning[-60:]))
                        last_shown = now
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        # The model is done: show nothing until it thinks again.
        progress.think("")
        self._note_usage(
            getattr(usage, "prompt_tokens", None) if usage else None,
            getattr(usage, "completion_tokens", None) if usage else None,
        )
        return "".join(content)


def _reasoning_piece(delta: Any) -> str:
    """The reasoning text in one streamed delta, whichever field the server uses."""
    for name in ("reasoning", "reasoning_content"):
        value = getattr(delta, name, None)
        if value is None:
            value = (getattr(delta, "model_extra", None) or {}).get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _groq_usage(chunk: Any) -> Any:
    """Groq reports a stream's token usage under ``x_groq.usage`` on the last chunk."""
    extra = (getattr(chunk, "model_extra", None) or {}).get("x_groq") or {}
    usage = extra.get("usage") if isinstance(extra, dict) else None
    if isinstance(usage, dict):
        from types import SimpleNamespace

        return SimpleNamespace(**usage)
    return None


class AnthropicProvider(LLMProvider):
    """Anthropic Claude via the ``anthropic`` SDK."""

    #: Upper bound on the reply; the prompts here return gate lists or a
    #: modest JSON object, so this is generous rather than tight.
    max_tokens = 8192

    def __init__(self, model_name: str = "claude-sonnet-5", api_key: str | None = None):
        self.model_name = model_name
        self._api_key = (api_key or "").strip() or None
        self._client = None

    @property
    def label(self) -> str:
        return "Anthropic"

    def _get_client(self):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise ImportError("Please install anthropic to use AnthropicProvider")

        if not self._client:
            api_key = self._api_key or _env_key_for("anthropic")
            if not api_key:
                raise ValueError(
                    "No Anthropic API key available: none was supplied with the "
                    "request and ANTHROPIC_API_KEY is not set."
                )
            self._client = Anthropic(api_key=api_key, timeout=request_timeout_s(), max_retries=0)
        return self._client

    def list_models(self) -> list[dict[str, str]]:
        client = self._get_client()

        def call() -> list[dict[str, str]]:
            found: list[dict[str, str]] = []
            for model in client.models.list(limit=100):
                model_id = str(getattr(model, "id", "") or "")
                if model_id:
                    found.append(
                        {"id": model_id, "label": str(getattr(model, "display_name", "") or model_id)}
                    )
            return found

        return self._with_retry(call)

    def complete(
        self,
        prompt: str,
        *,
        json_mode: bool = False,
        max_output_tokens: int | None = None,
        max_retries: int = 3,
    ) -> str:
        client = self._get_client()
        system = None
        if json_mode:
            system = (
                "Respond with a single JSON object and nothing else: no prose, "
                "no markdown fences."
            )

        def call() -> str:
            kwargs: dict[str, Any] = {}
            if system:
                kwargs["system"] = system
            response = client.messages.create(
                model=self.model_name,
                max_tokens=max_output_tokens or self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            usage = getattr(response, "usage", None)
            self._note_usage(
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
            )
            return "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )

        return self._with_retry(call, max_retries)


_log = logging.getLogger(__name__)


def parse_llm_fallbacks(raw: str | None) -> list[dict[str, Any]] | None:
    """Backup credentials from a form field holding a JSON list, or None if blank."""
    import json

    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"llm_fallbacks is not valid JSON: {exc.msg}") from None
    if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
        raise ValueError("llm_fallbacks must be a JSON list of objects")
    if len(data) > MAX_LLM_KEYS - 1:
        raise ValueError(f"At most {MAX_LLM_KEYS - 1} backup LLM keys are allowed")
    return data


class FallbackProvider(LLMProvider):
    """Several credentials behind one provider: when a key fails, try the next.

    A key is abandoned for the next one when its call ends in
    :class:`LLMUnavailableError` -- rate or quota limit, rejected key, no
    credits, a provider outage -- except when the request itself was too
    large, which no other key would accept either. The key that last
    worked stays first in line, so once a key runs dry every later call in
    the same run goes straight to one that still works. Only when every key
    has failed on the same call is the last error raised.
    """

    def __init__(self, configs: list[LLMConfig]):
        if not configs:
            raise ValueError("FallbackProvider needs at least one config")
        self._configs = [replace(c, fallbacks=()) for c in configs]
        self._providers: list[LLMProvider | None] = [None] * len(self._configs)
        self._active = 0
        self.last_usage = None

    def _provider(self, i: int) -> LLMProvider:
        if self._providers[i] is None:
            self._providers[i] = create_provider(self._configs[i])
        return self._providers[i]  # type: ignore[return-value]

    @property
    def active_index(self) -> int:
        """0 for the main key, 1.. for the backups."""
        return self._active

    @property
    def model_name(self) -> str:  # type: ignore[override]
        return self._configs[self._active].model

    @property
    def label(self) -> str:
        return self._configs[self._active].spec.label

    def _each(self, fn):
        n = len(self._configs)
        last: Exception | None = None
        for step in range(n):
            i = (self._active + step) % n
            try:
                provider = self._provider(i)
                result = fn(provider)
            except ImportError as exc:
                last = LLMUnavailableError(self._configs[i].spec.label, str(exc))
            except LLMUnavailableError as exc:
                if is_request_too_large(exc.cause):
                    raise
                last = exc
            else:
                if i != self._active:
                    _log.info("LLM: switched to key %d of %d (%s)", i + 1, n,
                              self._configs[i].describe())
                    self._active = i
                self.last_usage = getattr(provider, "last_usage", None)
                return result
            _log.warning("LLM key %d of %d failed: %s", i + 1, n, last)
        assert last is not None
        raise last

    def complete(
        self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None
    ) -> str:
        return self._each(lambda p: p.complete(
            prompt, json_mode=json_mode, max_output_tokens=max_output_tokens))

    def list_models(self) -> list[dict[str, str]]:
        return self._each(lambda p: p.list_models())


def create_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate the provider a resolved :class:`LLMConfig` names.

    A config with backup keys gets a :class:`FallbackProvider` that moves on
    to the next key whenever one fails.
    """
    if config.fallbacks:
        return FallbackProvider([config, *config.fallbacks])
    if config.provider == "gemini":
        return GeminiProvider(model_name=config.model, api_key=config.api_key)
    if config.provider == "anthropic":
        return AnthropicProvider(model_name=config.model, api_key=config.api_key)
    if config.provider in ("groq", "openai", "nvidia", "openai_compatible"):
        return OpenAIProvider(
            model_name=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            label=config.spec.label,
        )
    raise ValueError(f"Unsupported LLM provider {config.provider!r}")


def provider_from_request(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    fallbacks: list[dict[str, Any]] | None = None,
) -> LLMProvider | None:
    """Convenience: resolve and build in one step; None when no key exists."""
    config = resolve_llm_config(
        provider=provider, api_key=api_key, model=model, base_url=base_url,
        fallbacks=fallbacks,
    )
    return create_provider(config) if config else None
