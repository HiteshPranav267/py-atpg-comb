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

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

# Transient failures (over capacity, rate limited, gateway hiccups) worth
# retrying. Matched as substrings against the exception text, which is the
# lowest common denominator across the SDKs.
_RETRYABLE_MARKERS = (
    "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL",
    "502", "504", "overloaded", "rate limit", "RateLimit", "Timeout", "timed out",
)

# "The account needs attention" -- out of credits / over quota, the key
# rejected, or no key at all -- across every provider's own wording for it.
# Matched case-insensitively. Deliberately heuristic: providers don't share
# an error taxonomy, so a message that slips past these is just described
# generically, which is safe rather than wrong.
_QUOTA_MARKERS = (
    "insufficient_quota", "exceeded your current quota", "quota exceeded",
    "credit balance", "billing",
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
#: the key, so :meth:`LLMProvider._with_retry` fails fast on them, and the
#: batch runner (``pyatpg.automation.retry``) reports them as a distinct
#: "credentials" category rather than a transient blip.
ACCOUNT_ERROR_MARKERS = _QUOTA_MARKERS + _AUTH_MARKERS + _NO_KEY_MARKERS


class LLMUnavailableError(RuntimeError):
    """The model could not be run at all, so nothing it would have said exists.

    Raised by :meth:`LLMProvider._with_retry` once a provider call has
    failed for good -- out of credits, rate limited, the service returning
    a 5xx, the key rejected. This is deliberately *not* something the ATPG
    pipeline degrades around: a run the user asked to be LLM-assisted is
    aborted and told why, rather than silently substituting the structural
    heuristic and presenting that as the answer. The message is what the
    user sees; ``reason`` is the short human-readable cause and ``cause``
    the provider's original wording, kept so a batch runner can still
    classify it (see ``pyatpg.automation.retry.classify_error``).
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
#: look like any known vendor's. Gemini keeps the pre-multi-provider default.
DEFAULT_PROVIDER = "gemini"

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
    "gemini": ProviderSpec(
        id="gemini",
        label="Google Gemini",
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        default_model="gemini-flash-latest",
        key_prefix="AIza",
        hint="Keys from aistudio.google.com start with AIza.",
        models=("gemini-flash-latest", "gemini-2.5-pro", "gemini-2.5-flash-lite"),
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
        hint="Groq, OpenRouter, Together, Ollama, vLLM, LM Studio... anything "
             "exposing /v1/chat/completions. Set the base URL and model.",
        # No fixed model list -- every backend names models differently --
        # but the endpoints below are common enough to save typing.
        base_url_presets=(
            ("Ollama (local)", "http://localhost:11434/v1"),
            ("LM Studio (local)", "http://localhost:1234/v1"),
            ("Groq", "https://api.groq.com/openai/v1"),
            ("OpenRouter", "https://openrouter.ai/api/v1"),
            ("Together AI", "https://api.together.xyz/v1"),
        ),
    ),
}

#: Order the UI should list providers in.
PROVIDER_ORDER = ("gemini", "openai", "anthropic", "nvidia", "openai_compatible")


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
        "groq": "openai_compatible",
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

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.provider]

    def describe(self) -> str:
        """Human-readable, credential-free summary for notes and reports."""
        return f"{self.spec.label} / {self.model}"


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
) -> LLMConfig | None:
    """Work out which provider/key/model to use for a request.

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
        resolved_provider = requested_provider or env_provider
        if key is None:
            search = [resolved_provider] if resolved_provider else []
            search += [pid for pid in PROVIDER_ORDER if pid not in search]
            for pid in search:
                env_key = _env_key_for(pid)
                if env_key:
                    key = env_key
                    if resolved_provider is None:
                        resolved_provider = pid
                    break
        if key is None:
            return None
        resolved_provider = (
            resolved_provider or detect_provider_from_key(key) or DEFAULT_PROVIDER
        )

    spec = PROVIDER_SPECS[resolved_provider]

    resolved_model = _first_set(
        model, fallback_model, os.environ.get(LLM_MODEL_ENV_VAR), spec.default_model
    )
    if not resolved_model:
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

    return LLMConfig(
        provider=resolved_provider,
        api_key=key,
        model=resolved_model,
        base_url=resolved_base_url,
    )


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
    def complete(self, prompt: str, *, json_mode: bool = False) -> str:
        """Send the prompt and return the raw text response.

        ``json_mode`` asks the backend for a JSON object response where the
        provider supports it; callers must still validate the result, since
        not every provider can enforce it.
        """
        ...

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
                last_error = e
                text = str(e)
                account_problem = any(m in text.lower() for m in ACCOUNT_ERROR_MARKERS)
                retryable = not account_problem and any(m in text for m in _RETRYABLE_MARKERS)
                if not retryable or attempt == max_retries - 1:
                    raise LLMUnavailableError(self.label, text) from e
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
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
            self._client = genai.Client(api_key=api_key)
        return self._client

    def complete(self, prompt: str, *, json_mode: bool = False, max_retries: int = 3) -> str:
        client = self._get_client()
        config = None
        if json_mode:
            from google.genai import types as genai_types

            config = genai_types.GenerateContentConfig(response_mime_type="application/json")

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
            self._client = OpenAI(api_key=api_key, base_url=self._base_url)
        return self._client

    def complete(self, prompt: str, *, json_mode: bool = False, max_retries: int = 3) -> str:
        client = self._get_client()
        # Only the real OpenAI endpoint is guaranteed to honour
        # response_format; compatible servers frequently reject it, and the
        # prompts already spell out the expected JSON shape.
        kwargs: dict[str, Any] = {}
        if json_mode and self._base_url is None:
            kwargs["response_format"] = {"type": "json_object"}

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
            self._client = Anthropic(api_key=api_key)
        return self._client

    def complete(self, prompt: str, *, json_mode: bool = False, max_retries: int = 3) -> str:
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
                max_tokens=self.max_tokens,
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


def create_provider(config: LLMConfig) -> LLMProvider:
    """Instantiate the provider a resolved :class:`LLMConfig` names."""
    if config.provider == "gemini":
        return GeminiProvider(model_name=config.model, api_key=config.api_key)
    if config.provider == "anthropic":
        return AnthropicProvider(model_name=config.model, api_key=config.api_key)
    if config.provider in ("openai", "nvidia", "openai_compatible"):
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
) -> LLMProvider | None:
    """Convenience: resolve and build in one step; None when no key exists."""
    config = resolve_llm_config(
        provider=provider, api_key=api_key, model=model, base_url=base_url
    )
    return create_provider(config) if config else None
