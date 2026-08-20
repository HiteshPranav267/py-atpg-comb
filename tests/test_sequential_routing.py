"""Tests for how AtpgAnalysisService routes sequential circuits.

No network/API key is used here: both scenarios below are decided before
any LLM provider is ever constructed (no key configured; mode explicitly
disables LLM assistance), so they stay fast and deterministic like the rest
of the suite.
"""
from __future__ import annotations

from pathlib import Path

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService

BENCHMARKS = Path(__file__).parent.parent / "benchmarks"


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


def test_sequential_targeted_mode_without_a_key_explains_why_llm_did_not_run():
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
