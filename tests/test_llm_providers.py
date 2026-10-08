"""Provider selection: any vendor's key should "just work", and a server
pinned to one vendor must never forward a user's key for another vendor
to it."""
import pytest

from pyatpg.ranking.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    PROVIDER_ORDER,
    create_provider,
    detect_provider_from_key,
    normalize_provider_id,
    resolve_llm_config,
)

_ALL_ENV = (
    "LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "NVIDIA_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_ENV:
        monkeypatch.delenv(var, raising=False)


class TestKeySniffing:
    @pytest.mark.parametrize("key,expected", [
        ("AIzaSyExample", "gemini"),
        ("sk-proj-example", "openai"),
        ("sk-ant-api03-example", "anthropic"),  # must beat the "sk-" prefix
        ("nvapi-example", "nvidia"),
        ("totally-opaque", None),
        ("", None),
        (None, None),
    ])
    def test_prefix_maps_to_vendor(self, key, expected):
        assert detect_provider_from_key(key) == expected

    def test_unknown_prefix_falls_back_to_groq(self):
        config = resolve_llm_config(api_key="opaque")
        assert config.provider == "groq"
        assert config.model == "openai/gpt-oss-120b"

    def test_groq_key_is_detected(self):
        config = resolve_llm_config(api_key="gsk_abc")
        assert config.provider == "groq"
        assert config.model == "openai/gpt-oss-120b"
        assert config.base_url == "https://api.groq.com/openai/v1"
        assert isinstance(create_provider(config), OpenAIProvider)

    def test_each_vendor_key_resolves_and_builds(self):
        for key, cls in [
            ("AIza1", GeminiProvider),
            ("sk-1", OpenAIProvider),
            ("sk-ant-1", AnthropicProvider),
            ("nvapi-1", OpenAIProvider),
        ]:
            config = resolve_llm_config(api_key=key)
            assert isinstance(create_provider(config), cls)
            assert config.model  # every vendor has a default model

    def test_nvidia_points_at_nim_endpoint(self):
        config = resolve_llm_config(api_key="nvapi-1")
        assert config.base_url == "https://integrate.api.nvidia.com/v1"


class TestProviderNames:
    def test_aliases_and_case(self):
        assert normalize_provider_id("Claude") == "anthropic"
        assert normalize_provider_id("Google Gemini") == "gemini"
        assert normalize_provider_id("NVIDIA NIM") == "nvidia"
        assert normalize_provider_id("ollama") == "openai_compatible"

    def test_auto_and_blank_mean_unspecified(self):
        assert normalize_provider_id("auto") is None
        assert normalize_provider_id("") is None
        assert normalize_provider_id(None) is None

    def test_unknown_name_is_rejected(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            normalize_provider_id("bogus")

    def test_explicit_provider_beats_key_prefix(self):
        # A user who says "this is an OpenAI-compatible key" is believed
        # even when the key looks like something else.
        config = resolve_llm_config(
            provider="openai_compatible", api_key="AIza1",
            model="llama3", base_url="http://localhost:11434/v1",
        )
        assert config.provider == "openai_compatible"
        assert config.base_url == "http://localhost:11434/v1"


class TestPrecedence:
    def test_no_key_anywhere_means_no_llm(self):
        assert resolve_llm_config() is None

    def test_request_key_beats_environment(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
        config = resolve_llm_config(api_key="sk-ant-request")
        assert (config.provider, config.api_key) == ("anthropic", "sk-ant-request")

    def test_server_pinned_provider_does_not_capture_a_foreign_request_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env")
        assert resolve_llm_config().provider == "nvidia"
        assert resolve_llm_config(api_key="AIza-user").provider == "gemini"
        assert resolve_llm_config(api_key="sk-ant-user").provider == "anthropic"

    def test_server_pinned_provider_claims_an_opaque_request_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        assert resolve_llm_config(api_key="opaque").provider == "nvidia"

    def test_vendor_specific_env_var_implies_its_provider(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        config = resolve_llm_config()
        assert (config.provider, config.api_key) == ("openai", "sk-env")

    def test_generic_env_key_uses_env_provider(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "opaque")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
        config = resolve_llm_config()
        assert (config.provider, config.model) == ("anthropic", "claude-opus-5")

    def test_requested_provider_picks_its_own_env_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
        # The first provider in PROVIDER_ORDER that has an env key.
        assert resolve_llm_config().provider == "gemini"
        assert resolve_llm_config(provider="anthropic").api_key == "sk-ant-env"

    def test_model_override_wins_over_default(self):
        assert resolve_llm_config(api_key="sk-1", model="gpt-4.1").model == "gpt-4.1"


class TestCustomProvider:
    def test_needs_model_and_base_url(self):
        with pytest.raises(ValueError, match="model"):
            resolve_llm_config(provider="openai_compatible", api_key="x")
        with pytest.raises(ValueError, match="base URL"):
            resolve_llm_config(provider="openai_compatible", api_key="x", model="m")

    def test_env_supplies_model_and_base_url(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "llama3")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        config = resolve_llm_config(provider="custom", api_key="x")
        assert (config.model, config.base_url) == ("llama3", "http://localhost:11434/v1")


class TestServicesAcceptAnyProvider:
    """The ATPG entry points take a ready provider, not a Gemini client."""

    class _Stub:
        model_name = "stub"

        def __init__(self, text):
            self.text = text

        def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
            return self.text

    def test_circuit_analyst_uses_injected_provider(self):
        from pyatpg.atpg.circuit_analyst import CircuitAnalyst

        analyst = CircuitAnalyst(provider=self._Stub(
            '{"architecture_summary": "two gates", "observations": [], '
            '"suspicious_regions": [], "reasoning": []}'
        ))
        analysis = analyst.analyze(
            circuit_source="", profiles=[{"gate_id": "g1", "features": {}, "profile": ""}],
            stats={}, circuit_name="c",
        )
        assert analysis.source == "llm"
        assert analysis.model == "stub"
        assert analysis.architecture_summary == "two gates"

    def test_ranking_service_routes_provider_fields(self, monkeypatch):
        from pyatpg.api.services.ranking_service import RankingService

        ranker = RankingService.get_ranker(
            "LLMRanker", api_key="opaque", llm_provider="anthropic", llm_model="claude-opus-5"
        )
        assert isinstance(ranker.provider, AnthropicProvider)
        assert ranker.provider.model_name == "claude-opus-5"

    def test_ranking_service_without_any_key_is_a_clear_error(self):
        from pyatpg.api.services.ranking_service import RankingService

        with pytest.raises(ValueError, match="No LLM API key"):
            RankingService.get_ranker("LLMRanker")


class TestListModels:
    """The Settings picker asks the vendor for exact model ids."""

    def test_gemini_keeps_only_generate_content_models(self):
        from types import SimpleNamespace as NS
        from unittest.mock import patch

        from pyatpg.ranking.providers import GeminiProvider

        listed = [
            NS(name="models/gemini-2.5-flash", display_name="Gemini 2.5 Flash",
               supported_actions=["generateContent", "countTokens"]),
            NS(name="models/gemini-embedding-001", display_name="Embedding",
               supported_actions=["embedContent"]),
            NS(name="models/gemini-3.1-flash-lite", display_name="Gemini 3.1 Flash Lite",
               supported_actions=["generateContent"]),
        ]
        provider = GeminiProvider(api_key="AIzaTEST")
        fake_client = NS(models=NS(list=lambda: listed))
        with patch.object(GeminiProvider, "_get_client", return_value=fake_client):
            models = provider.list_models()
        assert [m["id"] for m in models] == ["gemini-2.5-flash", "gemini-3.1-flash-lite"]
        assert models[0]["label"] == "Gemini 2.5 Flash"

    def test_endpoint_uses_the_request_key_and_sniffs_the_vendor(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from pyatpg.api.app import app

        class Fake:
            model_name = "x"

            def list_models(self):
                return [{"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"}]

        with patch("pyatpg.api.routes.llm.create_provider", lambda cfg: Fake()):
            res = TestClient(app).post("/api/llm/models", json={"api_key": "AIzaTEST"})
        assert res.status_code == 200
        body = res.json()
        assert body["provider"] == "gemini"
        assert body["models"][0]["id"] == "gemini-2.5-flash"

    def test_endpoint_without_any_key_is_a_400(self, monkeypatch):
        from fastapi.testclient import TestClient

        from pyatpg.api.app import app
        from pyatpg.ranking import providers as P

        for spec in P.PROVIDER_SPECS.values():
            for var in spec.env_vars:
                monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(P.LLM_API_KEY_ENV_VAR, raising=False)
        res = TestClient(app).post("/api/llm/models", json={"provider": "gemini"})
        assert res.status_code == 400

    def test_endpoint_reports_a_vendor_failure_as_502(self):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from pyatpg.api.app import app
        from pyatpg.ranking.providers import LLMUnavailableError

        class Broken:
            def list_models(self):
                raise LLMUnavailableError("Gemini", "401 invalid api key")

        with patch("pyatpg.api.routes.llm.create_provider", lambda cfg: Broken()):
            res = TestClient(app).post("/api/llm/models", json={"api_key": "AIzaTEST"})
        assert res.status_code == 502
        assert "rejected the API key" in res.json()["detail"]
