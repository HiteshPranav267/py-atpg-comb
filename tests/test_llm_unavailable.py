"""When the LLM provider itself cannot be run -- out of credits, rate
limited, a 5xx from the service, a rejected key -- an LLM-assisted ATPG run
must stop and say why, not quietly substitute the offline structural
heuristic and present that as the answer.

A model that *ran* but returned something unusable is a different case and
still degrades: those tests live alongside the abort tests so the boundary
between the two is pinned down.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService
from pyatpg.atpg.circuit_analyst import CircuitAnalyst
from pyatpg.atpg.structural import parse_circuit, profile_circuit
from pyatpg.atpg.targeting import FaultTargetSelector
from pyatpg.automation.retry import Classification, classify_error
from pyatpg.ranking import providers as P
from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"

COMB = """
module tiny(a, b, c, d, out);
input a, b, c, d;
output out;
wire n1, n2;
and g1(n1, a, b);
or g2(n2, c, d);
xor g3(out, n1, n2);
endmodule
"""


class _FailingProvider(LLMProvider):
    """Goes through the real ``_with_retry`` path, failing the way an SDK would."""

    model_name = "failing-model"

    def __init__(self, sdk_message: str):
        self.sdk_message = sdk_message
        self.calls = 0

    @property
    def label(self) -> str:
        return "Stub"

    def complete(self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None) -> str:
        def call() -> str:
            self.calls += 1
            raise RuntimeError(self.sdk_message)

        return self._with_retry(call)


class _GarbageProvider:
    """The model ran fine but said nothing parseable."""

    model_name = "garbage-model"

    def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
        return "<<not json, not gate ids>>"


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(P.time, "sleep", lambda _s: None)


def _profiles():
    parsed = parse_circuit(COMB, "tiny.v")
    return profile_circuit(parsed, toggle_patterns=50).gate_profiles


# --------------------------------------------------------------- provider layer
class TestProviderRaisesLLMUnavailable:
    @pytest.mark.parametrize(
        "sdk_message, expected_reason",
        [
            ("429 RESOURCE_EXHAUSTED: rate limit hit", "rate-limited the request (HTTP 429)"),
            ("503 UNAVAILABLE: The model is overloaded", "unavailable or overloaded (HTTP 503)"),
            ("500 INTERNAL: something broke", "internal server error (HTTP 500)"),
            (
                "Error code: 429 - You exceeded your current quota, please check your "
                "plan and billing details.",
                "out of credits or over its quota",
            ),
            ("Your credit balance is too low to access the API.", "out of credits or over its quota"),
            ("401 Unauthorized: invalid x-api-key", "rejected the API key"),
            ("No Gemini API key available: none was supplied", "no API key was configured"),
            ("connection reset by peer", "request to the LLM provider failed"),
        ],
    )
    def test_final_failure_is_an_llm_unavailable_error_with_a_reason(
        self, sdk_message, expected_reason
    ):
        with pytest.raises(LLMUnavailableError) as info:
            _FailingProvider(sdk_message).complete("hi")
        err = info.value
        assert expected_reason in err.reason
        assert str(err).startswith("LLM could not be run: ")
        assert "was stopped rather than falling back" in str(err)
        # The provider's own wording travels along, so batch classification
        # and a curious user both still see the real cause.
        assert sdk_message in str(err)
        assert err.cause == sdk_message

    def test_it_is_still_a_runtime_error_for_older_callers(self):
        assert issubclass(LLMUnavailableError, RuntimeError)

    def test_transient_errors_are_retried_before_giving_up(self):
        provider = _FailingProvider("503 UNAVAILABLE")
        with pytest.raises(LLMUnavailableError):
            provider.complete("hi")
        assert provider.calls == 3

    def test_account_errors_fail_fast_without_retrying(self):
        # Vendors report an exhausted balance *as* a 429; that must not burn
        # the retry budget -- no amount of waiting refills the account.
        provider = _FailingProvider(
            "Error code: 429 - You exceeded your current quota, please check billing."
        )
        with pytest.raises(LLMUnavailableError):
            provider.complete("hi")
        assert provider.calls == 1


# ------------------------------------------------------------ batch classifier
class TestBatchClassification:
    def test_credit_failure_is_a_credentials_problem(self):
        with pytest.raises(LLMUnavailableError) as info:
            _FailingProvider("You exceeded your current quota, check billing.").complete("x")
        assert classify_error(info.value) is Classification.CREDENTIALS

    def test_capacity_failure_is_still_transient(self):
        with pytest.raises(LLMUnavailableError) as info:
            _FailingProvider("503 UNAVAILABLE: overloaded").complete("x")
        assert classify_error(info.value) is Classification.TRANSIENT


# ------------------------------------------------------- combinational layers
class TestTargetingDoesNotFallBack:
    def test_provider_failure_aborts_instead_of_using_the_heuristic(self):
        selector = FaultTargetSelector(provider=_FailingProvider("503 UNAVAILABLE"))
        with pytest.raises(LLMUnavailableError):
            selector.select(_profiles())

    def test_unusable_model_output_still_degrades(self):
        # The model *ran*; it just said nothing useful. That is not an
        # outage, and the structural heuristic remains the right fallback.
        selection = FaultTargetSelector(provider=_GarbageProvider()).select(_profiles())
        assert selection.strategy == "structural-heuristic"


class TestAnalystDoesNotFallBack:
    def test_provider_failure_aborts_instead_of_structural_analysis(self):
        analyst = CircuitAnalyst(provider=_FailingProvider("429 rate limit"))
        with pytest.raises(LLMUnavailableError):
            analyst.analyze(circuit_source="", profiles=_profiles(), stats={}, circuit_name="t")

    def test_unusable_model_output_still_degrades(self):
        analyst = CircuitAnalyst(provider=_GarbageProvider())
        analysis = analyst.analyze(
            circuit_source="", profiles=_profiles(), stats={}, circuit_name="t"
        )
        assert analysis.source == "structural"
        assert "Model analysis unavailable" in analysis.reasoning[0]


class TestServiceStopsTheRun:
    def test_combinational_run_aborts_before_atpg_starts(self, monkeypatch):
        monkeypatch.setattr(
            "pyatpg.atpg.service.create_provider",
            lambda _cfg: _FailingProvider("503 UNAVAILABLE: The model is overloaded"),
        )
        service = AtpgAnalysisService()
        with pytest.raises(LLMUnavailableError) as info:
            service.run(
                source_text=COMB, filename="tiny.v", mode=AnalysisMode.TARGETED,
                use_llm=True, api_key="fake-key-for-test",
            )
        assert "unavailable or overloaded (HTTP 503)" in str(info.value)

    def test_sequential_run_aborts_at_llm1(self, monkeypatch, tmp_path):
        # An empty LLM-1 cache so the provider is genuinely consulted.
        monkeypatch.setattr(
            "pyatpg.llm.circuit_context._DEFAULT_CACHE_PATH", str(tmp_path / "llm1.json")
        )
        monkeypatch.setattr(
            "pyatpg.seq.service.create_provider",
            lambda _cfg: _FailingProvider("Your credit balance is too low."),
        )
        service = AtpgAnalysisService()
        with pytest.raises(LLMUnavailableError) as info:
            service.run(
                source_text=(BENCHMARKS / "s27.v").read_text(), filename="s27.v",
                mode=AnalysisMode.TARGETED, use_llm=True, api_key="fake-key-for-test",
            )
        assert "out of credits" in str(info.value)


# ------------------------------------------------------------------ HTTP API
class TestApiSurfacesTheReason:
    def test_run_endpoint_returns_502_with_the_reason(self, monkeypatch):
        from fastapi.testclient import TestClient

        from pyatpg.api.app import app
        from pyatpg.api.routes import atpg as route

        def _boom(**_kw):
            raise LLMUnavailableError("Stub", "429 RESOURCE_EXHAUSTED: quota exceeded")

        monkeypatch.setattr(route._service, "run", _boom)
        client = TestClient(app)
        resp = client.post(
            "/api/atpg/run",
            files={"file": ("tiny.v", COMB, "text/plain")},
            data={"mode": "targeted", "use_llm": "true", "api_key": "fake-key"},
        )
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert detail.startswith("LLM could not be run: ")
        assert "out of credits or over its quota" in detail
