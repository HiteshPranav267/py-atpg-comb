"""Thin client for the *deployed* PyATPG backend.

Everything that needs Atalanta or an LLM SDK runs on the server; a caller
using only this module needs `requests` and nothing else from this
project's heavy dependency list (no Atalanta binary, no google-genai/
openai/anthropic SDKs) -- those live on the machine running the backend,
not on whoever is driving it.

Used by ``scripts/run_via_api.py`` to run the full research pipeline
against a friend's own slice of circuits without them installing anything
beyond ``requests`` (and, if they want the ground-truth evaluation math
locally rather than trusting a server round-trip for it, ``networkx`` --
see that script's docstring).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError as exc:  # pragma: no cover - exercised via the error message itself
    raise ImportError(
        "pyatpg.automation.api_client needs the 'requests' package: pip install requests"
    ) from exc

#: A cold Render free-tier instance can take 30-60s to wake from sleep on
#: the first request after being idle; generous enough to not misclassify
#: a slow-but-successful wake-up as a failure.
DEFAULT_TIMEOUT = 120.0


class ApiError(RuntimeError):
    """A non-2xx response. The message is the backend's own error detail
    where available (FastAPI's ``{"detail": "..."}`` body), not just the
    HTTP status -- so :func:`pyatpg.automation.retry.classify_error` can
    still recognise provider-specific quota/auth phrasing that reached the
    client through it, the same as it would running locally.
    """


@dataclass
class ApiClient:
    """Talks to one deployment of the PyATPG FastAPI backend."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    session: Any = field(default=None)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.session is None:
            self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _check(self, response: "requests.Response") -> "requests.Response":
        if response.ok:
            return response
        detail = response.text
        try:
            body = response.json()
            if isinstance(body, dict) and "detail" in body:
                detail = body["detail"]
        except ValueError:
            pass
        raise ApiError(f"{response.status_code} from {response.request.method} {response.url}: {detail}")

    def health(self) -> bool:
        try:
            return self.session.get(self._url("/health"), timeout=self.timeout).ok
        except requests.RequestException:
            return False

    def get_llm_providers(self) -> dict[str, Any]:
        response = self.session.get(self._url("/api/llm/providers"), timeout=self.timeout)
        return self._check(response).json()

    def generate_trojan_dataset(
        self,
        circuit_path: Path,
        upload_name: str,
        *,
        variants: int,
        strategy: str,
        seed: int | None = None,
        verify_activation: bool = False,
    ) -> bytes:
        """Uploads ``circuit_path`` and returns the generated dataset as raw
        zip bytes (the same download the web app's Trojan Dataset page
        offers) -- the caller extracts it. ``upload_name`` controls both
        the parser chosen (by extension) and the dataset's folder name
        server-side, so pass a name distinct per strategy (e.g.
        ``f"{stem}__{strategy}{suffix}"``) to keep multiple strategies'
        variants from landing in the same folder.
        """
        with open(circuit_path, "rb") as handle:
            files = {"file": (upload_name, handle, "text/plain")}
            data = {
                "variants": str(variants),
                "strategy": strategy,
                "verify_activation": str(bool(verify_activation)).lower(),
            }
            if seed is not None:
                data["seed"] = str(seed)
            response = self.session.post(
                self._url("/api/trojan/generate"), files=files, data=data, timeout=self.timeout,
            )
        return self._check(response).content

    def run_atpg(
        self,
        circuit_path: Path,
        *,
        mode: str,
        patterns: int = 3000,
        use_llm: bool = True,
        backtrack_limit: int | None = None,
        api_key: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Runs the ATPG pipeline (Atalanta + LLM circuit analysis) on the
        server and returns the full analysis payload -- the same JSON the
        web app's ATPG page renders."""
        with open(circuit_path, "rb") as handle:
            files = {"file": (circuit_path.name, handle, "text/plain")}
            data = {
                "mode": mode,
                "patterns": str(patterns),
                "use_llm": str(bool(use_llm)).lower(),
            }
            if backtrack_limit is not None:
                data["backtrack_limit"] = str(backtrack_limit)
            if api_key:
                data["api_key"] = api_key
            if llm_provider:
                data["llm_provider"] = llm_provider
            if llm_model:
                data["llm_model"] = llm_model
            if llm_base_url:
                data["llm_base_url"] = llm_base_url
            response = self.session.post(
                self._url("/api/atpg/run"), files=files, data=data, timeout=self.timeout,
            )
        return self._check(response).json()

    def extract_features(self, circuit_path: Path) -> dict[str, Any]:
        """Structural feature extraction only -- no ATPG, no LLM. Used as
        the input ranking needs."""
        with open(circuit_path, "rb") as handle:
            files = {"file": (circuit_path.name, handle, "text/plain")}
            response = self.session.post(
                self._url("/api/features/extract"), files=files, timeout=self.timeout,
            )
        return self._check(response).json()

    def run_ranking(
        self,
        feature_set: dict[str, Any],
        ranker_name: str,
        *,
        api_key: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
    ) -> dict[str, Any]:
        """Ranks the gates in ``feature_set`` (as returned by
        :meth:`extract_features`) and returns the ranked list -- ground
        truth comparison is not part of this call; the caller does that
        locally against the dataset's own metadata.json."""
        payload: dict[str, Any] = {"feature_set": feature_set, "ranker_name": ranker_name}
        if api_key:
            payload["api_key"] = api_key
        if llm_provider:
            payload["llm_provider"] = llm_provider
        if llm_model:
            payload["llm_model"] = llm_model
        if llm_base_url:
            payload["llm_base_url"] = llm_base_url
        response = self.session.post(self._url("/api/ranking/run"), json=payload, timeout=self.timeout)
        return self._check(response).json()
