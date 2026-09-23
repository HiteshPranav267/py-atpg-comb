"""Tests for how AtpgAnalysisService routes sequential circuits.

Most scenarios here are decided before any LLM provider is ever
constructed (no key configured; mode explicitly disables LLM assistance),
so they stay fast, deterministic, and network-free. The one test that
needs a provider injects a stub: a provider that genuinely cannot be
reached now *aborts* the run (see ``LLMUnavailableError``) rather than
degrading, so a fake key against the real SDK is no longer a stand-in.
"""
from __future__ import annotations

from pathlib import Path

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"

#: Every env var providers.py's ``resolve_llm_config`` checks. A "no key
#: configured" test clears these -- providers.py loads .env, so a
#: developer's real local credentials would otherwise leak in.
_LLM_ENV_VARS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "NVIDIA_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "LLM_API_KEY", "LLM_PROVIDER",
    "LLM_MODEL", "LLM_BASE_URL",
)


def _s27_v() -> str:
    return (BENCHMARKS / "s27.v").read_text()


def test_sequential_mode_label_reflects_the_requested_mode():
    """Regression: the sequential path used to hardcode NON_TARGETED
    regardless of what was requested, so selecting "LLM Heuristic
    (Targeted)" in the UI silently ran (and was labeled) as non-targeted."""
    service = AtpgAnalysisService()
    analysis = service.run(
        source_text=_s27_v(), filename="s27.v", mode=AnalysisMode.TARGETED, use_llm=True
    )
    d = analysis.to_dict()
    assert d["mode"] == "targeted"
    assert d["mode_label"] == "LLM Heuristic (Targeted)"


def test_sequential_non_targeted_mode_explicitly_skips_llm():
    service = AtpgAnalysisService()
    analysis = service.run(
        source_text=_s27_v(), filename="s27.v", mode=AnalysisMode.NON_TARGETED, use_llm=True
    )
    d = analysis.to_dict()
    assert d["sequential"]["llm_usage"]["enabled"] is False
    assert any("non-targeted" in note for note in d["notes"])


def test_sequential_targeted_mode_without_a_key_explains_why_llm_did_not_run(monkeypatch):
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    service = AtpgAnalysisService()
    analysis = service.run(
        source_text=_s27_v(), filename="s27.v", mode=AnalysisMode.TARGETED, use_llm=True
    )
    d = analysis.to_dict()
    assert d["sequential"]["llm_usage"]["enabled"] is False
    assert any("no API key is configured" in note for note in d["notes"])


def test_sequential_response_still_has_full_coverage_regardless_of_mode():
    """The mode/LLM plumbing must never affect the deterministic result."""
    service = AtpgAnalysisService()
    for mode in (AnalysisMode.TARGETED, AnalysisMode.NON_TARGETED, AnalysisMode.COMPARE):
        analysis = service.run(source_text=_s27_v(), filename="s27.v", mode=mode, use_llm=False)
        d = analysis.to_dict()
        assert d["sequential"]["analysis"]["results"]["fault_coverage"] == 100.0


def test_sequential_compare_mode_without_a_key_explains_why_there_is_no_comparison(monkeypatch):
    for var in _LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    service = AtpgAnalysisService()
    analysis = service.run(
        source_text=_s27_v(), filename="s27.v", mode=AnalysisMode.COMPARE, use_llm=True
    )
    d = analysis.to_dict()
    assert d["sequential"]["comparison"] is None
    assert any("no API key is configured" in note for note in d["notes"])


def test_sequential_targeted_mode_with_a_key_shows_the_comparison_too(monkeypatch):
    """Regression: only Compare mode used to build the deterministic-vs-
    LLM-assisted comparison, so picking "LLM Heuristic (Targeted)" -- the
    natural choice for "I want the LLM to help" -- silently showed just a
    single run with no comparison, which is what a user actually wants to
    see. TARGETED and COMPARE must behave identically for sequential
    circuits: there is no separate "targeted fault subset" concept here."""

    class _Stub:
        model_name = "stub"

        def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
            return "stub circuit summary"

    monkeypatch.setattr("pyatpg.seq.service.create_provider", lambda _cfg: _Stub())
    service = AtpgAnalysisService()
    analysis = service.run(
        source_text=_s27_v(), filename="s27.v", mode=AnalysisMode.TARGETED,
        use_llm=True, api_key="fake-key-for-test",
    )
    d = analysis.to_dict()
    assert d["sequential"]["comparison"] is not None
    assert d["sequential"]["comparison"]["baseline"]["results"]["fault_coverage"] == 100.0
    assert d["sequential"]["comparison"]["llm_assisted"]["results"]["fault_coverage"] == 100.0
