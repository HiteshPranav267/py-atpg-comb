import os
import time
from abc import ABC, abstractmethod

# Gemini transient failures (over capacity, rate limited) worth retrying.
_RETRYABLE_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL")

#: Per-request timeout (ms) for the Gemini SDK's HTTP client. genai.Client()
#: leaves this unset by default, which httpx treats as "wait forever" -- a
#: degraded endpoint could then hang a single attempt indefinitely instead of
#: failing so the retry loop below runs, tying up the request (and, on a
#: single-worker deployment, the whole backend) well past any client-side
#: timeout. See the same constant in pyatpg.atpg.targeting.
_LLM_TIMEOUT_MS = 20_000


class LLMProvider(ABC):
    """Abstract base class for LLM providers.
    
    Can be implemented for Gemini, OpenAI, Claude, Ollama, etc.
    """

    @abstractmethod
    def complete(self, prompt: str) -> str:
        """Sends the prompt to the LLM and returns the raw string response."""
        ...


class GeminiProvider(LLMProvider):
    """Implementation of LLMProvider using Google Gemini SDK."""

    def __init__(self, model_name: str = "gemini-flash-latest", api_key: str | None = None):
        self.model_name = model_name
        self._client = None
        #: Caller-supplied credential, used ahead of the environment so a key
        #: can be changed per request without restarting the service.
        self._api_key = (api_key or "").strip() or None

    def _get_client(self):
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError:
            raise ImportError("Please install google-genai to use GeminiProvider")

        if not self._client:
            api_key = self._api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "No Gemini API key available: none was supplied with the request "
                    "and GEMINI_API_KEY is not set."
                )
            self._client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(timeout=_LLM_TIMEOUT_MS),
            )
        return self._client

    def complete(self, prompt: str, max_retries: int = 3) -> str:
        client = self._get_client()
        last_error: Exception = RuntimeError("Gemini API failure")
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt
                )
                return response.text
            except Exception as e:
                last_error = e
                retryable = any(marker in str(e) for marker in _RETRYABLE_MARKERS)
                if not retryable or attempt == max_retries - 1:
                    raise RuntimeError(f"Gemini API failure: {str(e)}") from e
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
        raise RuntimeError(f"Gemini API failure: {str(last_error)}") from last_error
