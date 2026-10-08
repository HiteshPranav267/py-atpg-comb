"""Backup LLM keys: when one key hits its limit, the next one takes over."""
import pytest

from pyatpg.ranking import providers as prov
from pyatpg.ranking.providers import (
    FallbackProvider,
    LLMConfig,
    LLMProvider,
    LLMUnavailableError,
    create_provider,
    parse_llm_fallbacks,
    resolve_llm_config,
)

_BASE_ENV = (
    "LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "NVIDIA_API_KEY", "OPENAI_COMPATIBLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _BASE_ENV:
        monkeypatch.delenv(var, raising=False)
        for n in range(2, 6):
            monkeypatch.delenv(f"{var}_{n}", raising=False)


class FakeProvider(LLMProvider):
    """Answers with its own key, or fails the way it was told to."""

    calls: list[str] = []

    def __init__(self, key: str, fail: str | None):
        self.key = key
        self.fail = fail
        self.model_name = "fake"

    def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
        FakeProvider.calls.append(self.key)
        if self.fail:
            raise LLMUnavailableError("Fake", self.fail)
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2}
        return f"answer from {self.key}"


def _fallback(monkeypatch, failures: dict[str, str]):
    """A FallbackProvider over keys k1..kN whose inner providers are fakes."""
    FakeProvider.calls = []
    real_create = prov.create_provider

    def fake_create(config):
        if config.fallbacks:
            return real_create(config)
        return FakeProvider(config.api_key, failures.get(config.api_key))

    monkeypatch.setattr(prov, "create_provider", fake_create)
    configs = [LLMConfig(provider="openai_compatible", api_key=k, model="m",
                         base_url="http://x/v1") for k in ["k1", "k2", "k3"]]
    return FallbackProvider(configs)


class TestRotation:
    def test_first_key_used_when_it_works(self, monkeypatch):
        p = _fallback(monkeypatch, {})
        assert p.complete("hi") == "answer from k1"
        assert FakeProvider.calls == ["k1"]

    def test_quota_error_moves_to_next_key_and_stays_there(self, monkeypatch):
        p = _fallback(monkeypatch, {"k1": "429 Rate limit reached: tokens per day"})
        assert p.complete("a") == "answer from k2"
        assert p.active_index == 1
        assert p.last_usage == {"prompt_tokens": 1, "completion_tokens": 2}
        FakeProvider.calls = []
        assert p.complete("b") == "answer from k2"
        assert FakeProvider.calls == ["k2"]  # the dry key is not retried first

    def test_rejected_key_is_skipped_too(self, monkeypatch):
        p = _fallback(monkeypatch, {"k1": "401 invalid api key", "k2": "429 quota"})
        assert p.complete("a") == "answer from k3"

    def test_all_keys_failing_raises(self, monkeypatch):
        p = _fallback(monkeypatch, {k: "429 quota" for k in ["k1", "k2", "k3"]})
        with pytest.raises(LLMUnavailableError):
            p.complete("a")
        assert FakeProvider.calls == ["k1", "k2", "k3"]

    def test_request_too_large_is_not_rotated(self, monkeypatch):
        # Another key would reject the same oversized request.
        p = _fallback(monkeypatch, {"k1": "413 Request too large for model"})
        with pytest.raises(LLMUnavailableError):
            p.complete("a")
        assert FakeProvider.calls == ["k1"]

    def test_wraps_around_to_earlier_keys(self, monkeypatch):
        failures = {"k1": "429 quota"}
        p = _fallback(monkeypatch, failures)
        p.complete("a")                      # k1 dry -> k2
        failures.clear()
        p._providers = [None] * 3            # rebuild fakes with new failures
        failures["k2"] = "429 quota"
        failures["k3"] = "429 quota"
        assert p.complete("b") == "answer from k1"  # k1 has recovered


class TestResolve:
    def test_request_backups_are_resolved_each_on_its_own(self):
        cfg = resolve_llm_config(
            provider="openai_compatible", api_key="gsk_main", model="openai/gpt-oss-120b",
            base_url="https://api.groq.com/openai/v1",
            fallbacks=[
                {"api_key": "gsk_second", "provider": "openai_compatible",
                 "model": "openai/gpt-oss-120b", "base_url": "https://api.groq.com/openai/v1"},
                {"api_key": "AIzaBackup"},        # vendor sniffed from the prefix
                {"api_key": ""},                  # empty slot: ignored
                {"api_key": "gsk_main"},          # duplicate of the main key: dropped
            ],
        )
        assert [f.api_key for f in cfg.fallbacks] == ["gsk_second", "AIzaBackup"]
        assert cfg.fallbacks[1].provider == "gemini"
        assert cfg.api_keys == ["gsk_main", "gsk_second", "AIzaBackup"]
        assert "(+2 backup keys)" in cfg.describe()
        assert isinstance(create_provider(cfg), FallbackProvider)

    def test_no_backups_gives_a_plain_provider(self):
        cfg = resolve_llm_config(api_key="AIzaOnly")
        assert cfg.fallbacks == ()
        assert not isinstance(create_provider(cfg), FallbackProvider)

    def test_at_most_five_keys(self):
        cfg = resolve_llm_config(
            api_key="AIza0", fallbacks=[{"api_key": f"AIza{i}"} for i in range(1, 8)])
        assert len(cfg.api_keys) == 5

    def test_bad_backup_names_its_slot(self):
        with pytest.raises(ValueError, match="LLM key 2"):
            resolve_llm_config(api_key="AIza0",
                               fallbacks=[{"api_key": "x", "provider": "openai_compatible",
                                           "model": "m"}])  # no base URL

    def test_numbered_env_vars_back_up_an_env_key(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "gsk_a")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY_2", "gsk_b")
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY_4", "gsk_d")  # gaps are fine
        monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        cfg = resolve_llm_config()
        assert cfg.api_keys == ["gsk_a", "gsk_b", "gsk_d"]
        # Same provider, model and endpoint as the main key (a custom endpoint
        # pointing at Groq is resolved as the Groq provider).
        assert {(f.provider, f.model, f.base_url) for f in cfg.fallbacks} == {
            ("groq", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1")}

    def test_generic_env_key_backups(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "AIza1")
        monkeypatch.setenv("LLM_API_KEY_2", "AIza2")
        assert resolve_llm_config().api_keys == ["AIza1", "AIza2"]

    def test_env_backups_not_attached_to_a_request_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY_2", "AIzaEnv2")
        assert resolve_llm_config(api_key="AIzaUser").fallbacks == ()


class TestParse:
    def test_blank_is_none(self):
        assert parse_llm_fallbacks("") is None
        assert parse_llm_fallbacks("  ") is None

    def test_list_of_objects(self):
        assert parse_llm_fallbacks('[{"api_key": "a"}]') == [{"api_key": "a"}]

    @pytest.mark.parametrize("raw", ["{", '{"api_key": "a"}', '["a"]',
                                     '[{"api_key":"1"},{"api_key":"2"},{"api_key":"3"},'
                                     '{"api_key":"4"},{"api_key":"5"}]'])
    def test_rejects_bad_input(self, raw):
        with pytest.raises(ValueError):
            parse_llm_fallbacks(raw)


class TestApi:
    COMB = "module t(a, b, y); input a, b; output y; and g1(y, a, b); endmodule\n"

    def _post(self, monkeypatch, data):
        from fastapi.testclient import TestClient

        from pyatpg.api.app import app
        from pyatpg.api.routes import atpg as route

        seen = {}

        def _capture(**kw):
            seen.update(kw)
            raise ValueError("stop here")  # mapped to 400; the kwargs are what matter

        monkeypatch.setattr(route._service, "run", _capture)
        resp = TestClient(app).post(
            "/api/atpg/run", files={"file": ("t.v", self.COMB, "text/plain")}, data=data)
        return resp, seen

    def test_atpg_route_forwards_backup_keys(self, monkeypatch):
        _, seen = self._post(monkeypatch, {
            "api_key": "gsk_main",
            "llm_fallbacks": '[{"api_key": "gsk_2", "provider": "openai_compatible"}]',
        })
        assert seen["llm_fallbacks"] == [{"api_key": "gsk_2", "provider": "openai_compatible"}]

    def test_atpg_route_without_backups(self, monkeypatch):
        _, seen = self._post(monkeypatch, {"api_key": "gsk_main"})
        assert seen["llm_fallbacks"] is None

    def test_atpg_route_rejects_malformed_backups(self, monkeypatch):
        resp, seen = self._post(monkeypatch, {"llm_fallbacks": "not json"})
        assert resp.status_code == 400
        assert "llm_fallbacks" in resp.json()["detail"]
        assert seen == {}

    def test_llm_ranker_gets_a_rotating_provider(self):
        from pyatpg.api.services.ranking_service import RankingService

        ranker = RankingService.get_ranker(
            "LLMRanker", api_key="AIza1", llm_fallbacks=[{"api_key": "AIza2"}])
        assert isinstance(ranker.provider, FallbackProvider)
