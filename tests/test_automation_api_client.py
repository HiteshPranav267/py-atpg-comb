"""ApiClient against a fake requests.Session -- no real network call is
ever made here. Confirms the request shapes (multipart fields, JSON body)
match what pyatpg/api/routes/*.py actually expects, and that error
responses surface the backend's own detail text (so retry classification
can still recognise provider-specific quota/auth phrasing that reached the
client through it)."""
import json

import pytest

from pyatpg.automation.api_client import ApiClient, ApiError


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_body = json_body
        self.content = content
        self.text = text if text else (json.dumps(json_body) if json_body is not None else "")
        self.url = "https://fake/api/x"
        self.request = type("R", (), {"method": "POST"})()

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, response):
        self.responses.append(response)

    def _next(self):
        return self.responses.pop(0) if self.responses else _FakeResponse(200, {})

    def get(self, url, timeout=None, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, timeout=None, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next()


class TestHealth:
    def test_true_on_ok(self):
        session = _FakeSession()
        session.queue(_FakeResponse(200))
        client = ApiClient("https://x", session=session)
        assert client.health() is True

    def test_false_on_error_status(self):
        session = _FakeSession()
        session.queue(_FakeResponse(503))
        client = ApiClient("https://x", session=session)
        assert client.health() is False

    def test_false_on_network_exception(self):
        import requests

        class RaisingSession(_FakeSession):
            def get(self, *a, **k):
                raise requests.ConnectionError("no route to host")

        client = ApiClient("https://x", session=RaisingSession())
        assert client.health() is False


class TestErrorHandling:
    def test_error_detail_is_extracted_from_json_body(self):
        session = _FakeSession()
        session.queue(_FakeResponse(400, json_body={"detail": "insufficient_quota: out of credits"}))
        client = ApiClient("https://x", session=session)
        with pytest.raises(ApiError, match="insufficient_quota"):
            client.get_llm_providers()

    def test_non_json_error_body_falls_back_to_raw_text(self):
        session = _FakeSession()
        session.queue(_FakeResponse(502, text="<html>Bad Gateway</html>"))
        client = ApiClient("https://x", session=session)
        with pytest.raises(ApiError, match="Bad Gateway"):
            client.get_llm_providers()

    def test_success_does_not_raise(self):
        session = _FakeSession()
        session.queue(_FakeResponse(200, json_body={"providers": []}))
        client = ApiClient("https://x", session=session)
        assert client.get_llm_providers() == {"providers": []}


class TestGenerateTrojanDataset:
    def test_uploads_expected_fields(self, tmp_path):
        circuit = tmp_path / "c17.bench"
        circuit.write_text("# fake circuit")
        session = _FakeSession()
        session.queue(_FakeResponse(200, content=b"PK\x03\x04fakezip"))
        client = ApiClient("https://x", session=session)

        result = client.generate_trojan_dataset(
            circuit, "c17__random.bench", variants=5, strategy="random", seed=42,
        )

        assert result == b"PK\x03\x04fakezip"
        method, url, kwargs = session.calls[0]
        assert method == "POST" and url.endswith("/api/trojan/generate")
        assert kwargs["data"]["variants"] == "5"
        assert kwargs["data"]["strategy"] == "random"
        assert kwargs["data"]["seed"] == "42"
        assert kwargs["data"]["verify_activation"] == "false"
        assert kwargs["files"]["file"][0] == "c17__random.bench"

    def test_omits_seed_when_none(self, tmp_path):
        circuit = tmp_path / "c17.bench"
        circuit.write_text("x")
        session = _FakeSession()
        session.queue(_FakeResponse(200, content=b"zip"))
        client = ApiClient("https://x", session=session)

        client.generate_trojan_dataset(circuit, "c17.bench", variants=1, strategy="random")

        _, _, kwargs = session.calls[0]
        assert "seed" not in kwargs["data"]


class TestRunAtpg:
    def test_forwards_llm_fields_only_when_present(self, tmp_path):
        circuit = tmp_path / "c17.bench"
        circuit.write_text("x")
        session = _FakeSession()
        session.queue(_FakeResponse(200, json_body={"job_id": "1"}))
        client = ApiClient("https://x", session=session)

        client.run_atpg(circuit, mode="compare", api_key="AIzaFake", llm_provider="gemini")

        _, url, kwargs = session.calls[0]
        assert url.endswith("/api/atpg/run")
        assert kwargs["data"]["api_key"] == "AIzaFake"
        assert kwargs["data"]["llm_provider"] == "gemini"
        assert "llm_model" not in kwargs["data"]

    def test_use_llm_false_sends_lowercase_string(self, tmp_path):
        circuit = tmp_path / "c17.bench"
        circuit.write_text("x")
        session = _FakeSession()
        session.queue(_FakeResponse(200, json_body={}))
        client = ApiClient("https://x", session=session)

        client.run_atpg(circuit, mode="compare", use_llm=False)

        _, _, kwargs = session.calls[0]
        assert kwargs["data"]["use_llm"] == "false"


class TestRunRanking:
    def test_sends_feature_set_and_ranker_name(self):
        session = _FakeSession()
        session.queue(_FakeResponse(200, json_body={"circuit_name": "c17", "ranker_name": "ScoapRanker",
                                                      "execution_time": 0.1, "ranked_gates": []}))
        client = ApiClient("https://x", session=session)

        result = client.run_ranking({"circuit_name": "c17", "gate_features": []}, "ScoapRanker")

        _, url, kwargs = session.calls[0]
        assert url.endswith("/api/ranking/run")
        assert kwargs["json"]["ranker_name"] == "ScoapRanker"
        assert kwargs["json"]["feature_set"]["circuit_name"] == "c17"
        assert "api_key" not in kwargs["json"]
        assert result["ranker_name"] == "ScoapRanker"

    def test_credentials_error_propagates_with_message_intact(self):
        session = _FakeSession()
        session.queue(_FakeResponse(400, json_body={
            "detail": "No LLM API key available: supply one with the request or set LLM_API_KEY.",
        }))
        client = ApiClient("https://x", session=session)
        with pytest.raises(ApiError, match="No LLM API key available"):
            client.run_ranking({"circuit_name": "c17", "gate_features": []}, "LLMRanker")


class TestMissingRequestsDependency:
    def test_helpful_error_without_requests(self, monkeypatch):
        import builtins
        import sys

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named 'requests'")
            return real_import(name, *args, **kwargs)

        # Force a fresh execution of the module's top level (where the
        # `import requests` line lives) rather than reusing the already
        # -imported module still sitting in sys.modules from earlier tests.
        monkeypatch.delitem(sys.modules, "pyatpg.automation.api_client", raising=False)
        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="pip install requests"):
            import pyatpg.automation.api_client  # noqa: F401
