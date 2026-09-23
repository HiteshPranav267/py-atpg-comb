"""Context reduction for the combinational LLM stages, and its accounting.

The targeting prompt used to carry every gate as a five-line paragraph, so
it scaled with the circuit (c7552 came to ~220k tokens). The compact CSV
encoding and the capped candidate pool keep it a few thousand tokens at
any size, the cache makes an identical re-run free, and the tracker
reports what was actually spent.
"""
import re

import pytest

from pyatpg.atpg import llm_context as C
from pyatpg.atpg.structural import parse_circuit, profile_circuit
from pyatpg.atpg.targeting import FaultTargetSelector
from pyatpg.llm.token_tracker import TokenTracker, approx_tokens
from pyatpg.ranking.providers import LLMProvider

from test_targeting import TROJAN


class _Fake(LLMProvider):
    model_name = "fake"

    def __init__(self, usage=None):
        self.prompts = []
        self._usage = usage

    def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
        self.prompts.append(prompt)
        self.last_usage = self._usage
        aliases = re.findall(r"^(g\d+),", prompt, flags=re.MULTILINE)
        return "\n".join(f"{a},0.9" for a in aliases[:5])


def _profile(text=TROJAN, **kw):
    return profile_circuit(parse_circuit(text, "t.v"), toggle_patterns=200, **kw)


# ---------------------------------------------------------------- encoding


def test_compact_row_carries_the_same_numbers_as_the_paragraph():
    p = _profile()
    gate = next(g for g in p.gate_profiles if g["gate_id"] == "ht_trig")
    row = C.compact_profile_line("g7", gate["features"])
    cols = dict(zip(C.COMPACT_COLUMNS, row.split(",")))
    assert cols["id"] == "g7"
    assert cols["type"] == "AND"
    assert int(cols["fan_in"]) == gate["features"]["fan_in"]
    assert int(cols["co"]) == gate["features"]["co"]
    assert float(cols["toggle_rate"]) == pytest.approx(gate["features"]["toggle_rate"], abs=1e-4)


def test_compact_encoding_is_a_fraction_of_the_paragraph():
    p = _profile()
    paragraphs = "\n".join(g["profile"] for g in p.gate_profiles)
    compact = C.compact_profile_block(
        [(str(g["gate_id"]), g["features"]) for g in p.gate_profiles]
    )
    assert approx_tokens(compact) * 2 < approx_tokens(paragraphs)


# ------------------------------------------------------------ candidate pool


def test_small_circuit_sends_every_gate():
    p = _profile()
    pool, notes = C.build_candidate_pool(p.gate_profiles, p.graph, limit=300)
    assert len(pool) == len(p.gate_profiles)
    assert "All" in notes[0]


def test_pool_is_capped_and_says_how_it_was_built():
    p = _profile()
    pool, notes = C.build_candidate_pool(p.gate_profiles, p.graph, limit=6)
    assert len(pool) == 6
    assert "6 of" in notes[0]
    assert C.CONTEXT_GATE_LIMIT_ENV_VAR in notes[0]


def test_must_include_gates_survive_the_cap():
    p = _profile()
    pool, _ = C.build_candidate_pool(
        p.gate_profiles, p.graph, limit=3, must_include=["ht_trig", "n5"]
    )
    ids = {str(g["gate_id"]) for g in pool}
    assert {"ht_trig", "n5"} <= ids


def test_trojan_gates_reach_the_pool_through_the_anomaly_view():
    """The planted trigger/payload are not the rarest gates in TROJAN, but
    they are out of line with their neighbourhood -- the pool must carry
    them even with a cap tighter than the rarity ranking alone would need."""
    p = _profile()
    by_id = {str(g["gate_id"]): g for g in p.gate_profiles}
    assert C._anomaly_score(by_id["ht_trig"], by_id, p.graph) > 0 or \
        C._anomaly_score(by_id["n5"], by_id, p.graph) > 0


def test_limit_zero_disables_the_cap(monkeypatch):
    monkeypatch.setenv(C.CONTEXT_GATE_LIMIT_ENV_VAR, "0")
    assert C.context_gate_limit() == 0
    monkeypatch.setenv(C.CONTEXT_GATE_LIMIT_ENV_VAR, "42")
    assert C.context_gate_limit() == 42
    assert C.context_gate_limit(7) == 7


# ------------------------------------------------------- selector + tracker


def test_prompt_uses_compact_rows_and_reports_context():
    p = _profile()
    fake = _Fake()
    tracker = TokenTracker()
    sel = FaultTargetSelector(provider=fake, tracker=tracker, cache=None).select(
        p.gate_profiles, graph=p.graph
    )
    prompt = fake.prompts[0]
    assert C.COMPACT_HEADER in prompt
    assert "Gate:" not in prompt  # the old paragraph form is gone
    assert sel.context["gates_sent"] == len(p.gate_profiles)
    assert tracker.total_calls == 1
    assert tracker.calls[0].stage == "targeting"


def test_provider_usage_report_is_preferred_over_the_estimate():
    p = _profile()
    fake = _Fake(usage={"prompt_tokens": 1234, "completion_tokens": 56})
    tracker = TokenTracker()
    FaultTargetSelector(provider=fake, tracker=tracker, cache=None).select(
        p.gate_profiles, graph=p.graph
    )
    summary = tracker.summary()
    assert summary["prompt_tokens_est"] == 1234
    assert summary["response_tokens_est"] == 56
    assert summary["tokens_exact"] is True


def test_missing_usage_report_falls_back_to_the_estimate():
    p = _profile()
    fake = _Fake(usage=None)
    tracker = TokenTracker()
    FaultTargetSelector(provider=fake, tracker=tracker, cache=None).select(
        p.gate_profiles, graph=p.graph
    )
    summary = tracker.summary()
    assert summary["prompt_tokens_est"] == approx_tokens(fake.prompts[0])
    assert summary["tokens_exact"] is False


def test_identical_rerun_is_served_from_cache(tmp_path):
    p = _profile()
    cache = C.LLMResultCache(storage_path=str(tmp_path / "cache.json"))
    fake = _Fake(usage={"prompt_tokens": 100, "completion_tokens": 10})
    tracker = TokenTracker()

    first = FaultTargetSelector(provider=fake, tracker=tracker, cache=cache).select(
        p.gate_profiles, graph=p.graph, source_text=TROJAN
    )
    second = FaultTargetSelector(provider=fake, tracker=tracker, cache=cache).select(
        p.gate_profiles, graph=p.graph, source_text=TROJAN
    )
    assert len(fake.prompts) == 1
    assert first.gates == second.gates
    assert not first.cached and second.cached
    assert second.strategy == "llm (cached)"
    summary = tracker.summary()
    assert summary["api_calls"] == 1 and summary["cached_hits"] == 1
    assert summary["total_tokens_est"] == 110


def test_cache_misses_on_a_different_netlist_or_model(tmp_path):
    p = _profile()
    cache = C.LLMResultCache(storage_path=str(tmp_path / "cache.json"))
    fake = _Fake()
    FaultTargetSelector(provider=fake, cache=cache).select(
        p.gate_profiles, graph=p.graph, source_text=TROJAN
    )
    FaultTargetSelector(provider=fake, cache=cache).select(
        p.gate_profiles, graph=p.graph, source_text=TROJAN + "\n// edited"
    )
    other = _Fake()
    other.model_name = "different"
    FaultTargetSelector(provider=other, cache=cache).select(
        p.gate_profiles, graph=p.graph, source_text=TROJAN
    )
    assert len(fake.prompts) == 2
    assert len(other.prompts) == 1


def test_no_source_text_means_no_caching():
    p = _profile()
    fake = _Fake()
    for _ in range(2):
        FaultTargetSelector(provider=fake).select(p.gate_profiles, graph=p.graph)
    assert len(fake.prompts) == 2
