"""Targeting: reproducible ranking, structural trojan detection, and the
guarantee that a detected suspect always reaches the fault list."""
import re

import pytest

from pyatpg.atpg.structural import parse_circuit, profile_circuit
from pyatpg.atpg.suspects import find_structural_suspects
from pyatpg.atpg.targeting import FaultTargetSelector


class _FakeProvider:
    """Stands in for any LLMProvider: records the prompt, returns canned text."""

    model_name = "fake-model"

    def __init__(self, respond):
        #: Either a fixed string or a callable taking the prompt.
        self._respond = respond
        self.prompts: list[str] = []

    def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
        self.prompts.append(prompt)
        return self._respond(prompt) if callable(self._respond) else self._respond
from pyatpg.core.graph_analysis import CircuitGraph, DEFAULT_TOGGLE_SEED


CLEAN = """
module tiny(a, b, c, d, e, out1, out2);
input a, b, c, d, e;
output out1, out2;
wire n1, n2, n3, n4, n5;
nand g1(n1, a, b);
nand g2(n2, c, d);
nand g3(n3, n1, n2);
not  g4(n4, n3);
nand g5(n5, n4, e);
nand g6(out1, n5, n1);
nand g7(out2, n3, n5);
endmodule
"""

# Same circuit with a combinational trojan spliced in: a rarely-satisfied
# AND over two nets, XORed into an existing signal path.
TROJAN = """
module tiny(a, b, c, d, e, out1, out2);
input a, b, c, d, e;
output out1, out2;
wire n1, n2, n3, n4, n5, n5_orig, ht_trig;
nand g1(n1, a, b);
nand g2(n2, c, d);
nand g3(n3, n1, n2);
not  g4(n4, n3);
nand g5(n5_orig, n4, e);
and  ht1(ht_trig, n1, n2);
xor  ht2(n5, n5_orig, ht_trig);
nand g6(out1, n5, n1);
nand g7(out2, n3, n5);
endmodule
"""


def _profile(src, **kw):
    return profile_circuit(parse_circuit(src, "tiny.v"), **kw)


def _ranking(profile):
    ordered = sorted(
        profile.gate_profiles,
        key=lambda p: (
            p["features"].get("toggle_rate", 1.0),
            -p["features"].get("co", 0),
        ),
    )
    return [p["gate_id"] for p in ordered]


class TestReproducibleRanking:
    def test_same_seed_gives_identical_ranking(self):
        a = _ranking(_profile(CLEAN, toggle_patterns=400))
        b = _ranking(_profile(CLEAN, toggle_patterns=400))
        assert a == b

    def test_different_seeds_are_each_reproducible(self):
        one = _ranking(_profile(CLEAN, toggle_patterns=400, toggle_seed=1))
        one_again = _ranking(_profile(CLEAN, toggle_patterns=400, toggle_seed=1))
        two = _ranking(_profile(CLEAN, toggle_patterns=400, toggle_seed=2))
        assert one == one_again
        assert one != two or len(one) < 3  # tiny circuits can coincide

    def test_seed_is_recorded_on_the_profile(self):
        p = _profile(CLEAN, toggle_patterns=400, toggle_seed=7)
        assert p.toggle_seed == 7
        assert p.toggle_patterns == 400
        assert _profile(CLEAN).toggle_seed == DEFAULT_TOGGLE_SEED

    def test_toggle_rates_are_cached_per_stimulus(self):
        graph = CircuitGraph(parse_circuit(CLEAN, "tiny.v").circuit)
        first = graph.compute_toggle_rates(num_patterns=200, seed=3)
        assert graph.compute_toggle_rates(num_patterns=200, seed=3) is first
        # A different stimulus must not silently return the cached result.
        assert graph.compute_toggle_rates(num_patterns=200, seed=4) is not first


class TestLargeCircuitSimulation:
    def test_every_gate_gets_simulated(self):
        """The old fixed-point loop bailed out above 1000 gates, leaving most
        gates with no toggle data at all on large circuits."""
        n = 1200
        lines = ["module wide(a, b, out);", "input a, b;", "output out;"]
        wires = [f"w{i}" for i in range(n)]
        lines.append("wire " + ", ".join(wires) + ";")
        lines.append("nand g0(w0, a, b);")
        for i in range(1, n):
            lines.append(f"nand g{i}(w{i}, w{i-1}, a);")
        lines.append(f"buf gout(out, w{n-1});")
        lines.append("endmodule")
        src = "\n".join(lines)

        graph = CircuitGraph(parse_circuit(src, "wide.v").circuit)
        toggle = graph.compute_toggle_rates(num_patterns=40, seed=1)
        # Every one of the chained gates must have real toggle data.
        assert len(toggle) >= n


class TestStructuralSuspects:
    def test_clean_circuit_has_no_suspects(self):
        p = _profile(CLEAN, toggle_patterns=600)
        gates, reasons = find_structural_suspects(p.gate_profiles, p.graph)
        assert gates == []
        assert reasons == []

    def test_trojan_shape_is_detected(self):
        """The detector localises the splice site.

        Any of the three inserted nets counts: the trigger (ht_trig), the
        payload XOR (n5), or the renamed original signal feeding it
        (n5_orig). Flagging any one of them puts an investigator on the
        inserted structure, which is what the detector is for -- it does not
        promise to name the trigger specifically.
        """
        p = _profile(TROJAN, toggle_patterns=600)
        gates, reasons = find_structural_suspects(p.gate_profiles, p.graph)
        assert {"ht_trig", "n5", "n5_orig"} & set(gates)
        assert reasons

    def test_detector_never_raises_without_a_graph(self):
        p = _profile(TROJAN, toggle_patterns=200)
        sel = FaultTargetSelector(api_key=None).select(p.gate_profiles, graph=None)
        assert sel.structural_suspects == []


class TestObfuscationInvariance:
    """The framework's premise is that gate-level structure, not naming,
    carries the signal. The model must therefore never see real gate names --
    otherwise a run measures the naming convention, not the reasoning."""

    def _capture_prompt(self, profiles):
        from pyatpg.atpg import targeting as T

        provider = _FakeProvider("g0,g1,g2")
        selection = T.FaultTargetSelector(provider=provider).select(profiles)
        return provider.prompts[0], selection

    def test_real_gate_names_never_reach_the_model(self):
        p = _profile(TROJAN, toggle_patterns=200)
        prompt, _ = self._capture_prompt(p.gate_profiles)
        import re

        for name in ("ht_trig", "n5_orig", "n1", "n2"):
            # A real name would appear as the id column of a CSV row.
            assert not re.search(rf"^{re.escape(name)},", prompt, flags=re.MULTILINE)
        assert "ht_" not in prompt.lower()

    def test_aliases_resolve_back_to_real_gates(self):
        p = _profile(TROJAN, toggle_patterns=200)
        _, selection = self._capture_prompt(p.gate_profiles)
        known = {str(g["gate_id"]) for g in p.gate_profiles}
        assert selection.gates
        for gate in selection.gates:
            assert gate in known

    def test_invented_aliases_are_dropped(self):
        from pyatpg.atpg import targeting as T

        scores = T.FaultTargetSelector._parse_scores(
            "g0, g99999, not_an_alias, ht_trig", {"g0": "real_gate"}
        )
        # Only the one real alias survives; a raw gate name is not an alias.
        assert scores == {"real_gate": 100.0}


class TestRequestTokenBudget:
    """A provider that refuses an oversized request rejects it before the
    model runs, so the stage has to ask for less rather than find out the
    hard way. Groq's free tier caps one request at 8000 tokens; a c3540-
    sized circuit went over that on the pool and the reply reservation
    together, and shrinking only the pool could not help because its floor
    is however many gates the answer still has to be able to name."""

    @staticmethod
    def _big_profiles(count=400):
        """Enough gates to sit above the pool and target-count floors."""
        return [
            {
                "gate_id": f"net{i}_inst_G{i}",
                "features": {
                    "gate_type": "NAND", "node_type": "gate",
                    "fan_in": 2, "fan_out": 3, "level": i % 90,
                    "cc0": 1000 + i, "cc1": 2000 + i, "co": 3000 + i,
                    "toggle_rate": (i % 100) / 1000.0,
                },
            }
            for i in range(count)
        ]

    def _run_with_budget(self, monkeypatch, budget):
        from pyatpg.atpg import targeting as T

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", str(budget))
        provider = _FakeProvider("g0,0.9\ng1,0.8")
        T.FaultTargetSelector(provider=provider).select(self._big_profiles())
        return provider.prompts[0]

    def test_a_tight_budget_produces_a_smaller_prompt(self, monkeypatch):
        generous = self._run_with_budget(monkeypatch, 0)  # 0 disables the check
        tight = self._run_with_budget(monkeypatch, 4000)
        assert len(tight) < len(generous)

    def test_the_trim_is_reported_rather_than_silent(self, monkeypatch):
        from pyatpg.atpg import targeting as T

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "4000")
        provider = _FakeProvider("g0,0.9")
        selection = T.FaultTargetSelector(provider=provider).select(self._big_profiles())
        notes = " ".join(selection.notes)
        assert "request budget" in notes

    def test_the_reply_bound_is_sent_and_scales_with_the_request(self, monkeypatch):
        from pyatpg.atpg import targeting as T

        seen = {}

        class _Recording(_FakeProvider):
            def complete(self, prompt, *, json_mode=False, max_output_tokens=None):
                seen["max_output_tokens"] = max_output_tokens
                return super().complete(prompt, json_mode=json_mode)

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "1500")
        p = _profile(TROJAN, toggle_patterns=200)
        T.FaultTargetSelector(provider=_Recording("g0,0.9")).select(p.gate_profiles)

        # Left unset, some providers reserve a large default against the
        # same budget and refuse the request outright.
        assert seen["max_output_tokens"] is not None
        assert seen["max_output_tokens"] > 0


class TestSuspectMerging:
    def test_suspects_reach_the_fault_list_without_an_llm(self):
        p = _profile(TROJAN, toggle_patterns=600)
        sel = FaultTargetSelector(api_key=None).select(
            p.gate_profiles, graph=p.graph
        )
        assert sel.structural_suspects
        for gate in sel.structural_suspects:
            assert gate in sel.gates

    def test_merging_is_reported_in_notes(self):
        p = _profile(TROJAN, toggle_patterns=600)
        sel = FaultTargetSelector(api_key=None).select(
            p.gate_profiles, graph=p.graph
        )
        assert any("structural pattern" in n or "trojan-shaped" in n for n in sel.notes)

    def test_suspects_survive_a_max_gates_cap_of_one(self):
        """The cap keeps the targeted run small; it must not drop a detection."""
        p = _profile(TROJAN, toggle_patterns=600)
        sel = FaultTargetSelector(api_key=None, max_gates=1).select(
            p.gate_profiles, graph=p.graph
        )
        for gate in sel.structural_suspects:
            assert gate in sel.gates


def _scoring_provider(score):
    """Scores every gate it is shown with ``score(alias_index)``."""
    import re

    def respond(prompt):
        aliases = re.findall(r"^g(\d+),", prompt, flags=re.MULTILINE)
        return "\n".join(f"g{a},{score(int(a))}" for a in aliases)

    return _FakeProvider(respond)


class TestHeuristicFloor:
    """LLM runs keep the heuristic's top picks; the model fills the rest of
    the same-size list with the gates it scores highest."""

    @staticmethod
    def _select(provider, monkeypatch, share="0.5"):
        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "0")
        monkeypatch.setenv("PYATPG_LLM_SHARE", share)
        profiles = TestRequestTokenBudget._big_profiles()
        llm = FaultTargetSelector(provider=provider).select(profiles)
        base = FaultTargetSelector(provider=None).select(profiles)
        return llm, base

    def test_both_modes_test_the_same_number_of_gates(self, monkeypatch):
        llm, base = self._select(_scoring_provider(lambda a: 90), monkeypatch)
        assert len(llm.gates) == len(base.gates) == 120  # 30% of 400
        floor = llm.context["floor"]
        assert floor == 60 and llm.gates[:floor] == base.gates[:floor]

    def test_low_scores_leave_their_slots_to_the_heuristic(self, monkeypatch):
        llm, base = self._select(_scoring_provider(lambda a: 90 if a < 5 else 10), monkeypatch)
        scores = llm.context["scores"]
        high = {g for g, s in scores.items() if s >= 30}  # 5 per batch
        assert 0 < len(high) < 60
        assert set(llm.context["llm_picks"]) == high
        assert len(llm.context["heuristic_fill"]) == 60 - len(high)
        assert len(llm.gates) == len(base.gates)

    def test_a_model_that_picks_nothing_gives_the_heuristic_list(self, monkeypatch):
        llm, base = self._select(_scoring_provider(lambda a: 0), monkeypatch)
        assert llm.used_llm and llm.gates == base.gates

    def test_picks_are_highest_score_first(self, monkeypatch):
        llm, _ = self._select(_scoring_provider(lambda a: a % 100), monkeypatch)
        scores = llm.context["scores"]
        picked = [scores[g] for g in llm.context["llm_picks"]]
        assert picked == sorted(picked, reverse=True)

    def test_zero_share_is_the_heuristic(self, monkeypatch):
        llm, base = self._select(_scoring_provider(lambda a: 90), monkeypatch, share="0")
        assert llm.gates == base.gates

    def test_scores_on_a_zero_to_one_scale_are_read_as_fractions(self):
        from pyatpg.atpg import targeting as T

        scores = T.FaultTargetSelector._parse_scores("g0,0.9\ng1,45\ng2,1", {
            "g0": "a", "g1": "b", "g2": "c"})
        assert scores == {"a": 90.0, "b": 45.0, "c": 1.0}

    def test_unscored_gates_are_asked_for_again(self, monkeypatch):
        calls = []

        def respond(prompt):
            import re

            aliases = re.findall(r"^(g\d+),", prompt, flags=re.MULTILINE)
            calls.append(len(aliases))
            # First answer leaves half the table out; the re-ask scores the rest.
            keep = aliases[: len(aliases) // 2] if len(calls) == 1 else aliases
            return "\n".join(f"{a},50" for a in keep)

        monkeypatch.setenv("PYATPG_LLM_BATCHES", "1")
        llm, _ = self._select(_FakeProvider(respond), monkeypatch)
        assert len(calls) == 2 and calls[1] == calls[0] - calls[0] // 2
        assert set(llm.context["scores"]) == set(llm.context["pool"])


class TestVerdict:
    def test_clean_circuit_returns_no_trojan(self):
        from pyatpg.atpg.verdict import build_verdict

        v = build_verdict([], [], [])
        assert v.verdict == "no_trojan_indicated"
        assert v.confidence == 0.0
        assert v.caveats

    def test_shape_alone_is_only_inconclusive(self):
        """The XOR-splice shape matches 63 benign gates in clean s15850."""
        from pyatpg.atpg.verdict import build_verdict

        v = build_verdict(
            ["ht_trig"], ["ht_trig: rarely-satisfied AND feeding an XOR"], []
        )
        assert v.verdict == "inconclusive"
        assert v.suspects[0].gate == "ht_trig"
        assert v.suspects[0].confidence < 0.5

    def test_trigger_evidence_makes_a_trojan_suspected(self):
        from pyatpg.atpg.trigger_detector import TriggerEvidence
        from pyatpg.atpg.verdict import build_verdict

        ev = TriggerEvidence()
        ev.add("tg_clk", "logic_clock", "clocks register tg although it is logic")
        ev.add("ht_trig", "never_toggling_cluster", "one of 6 never-toggling gates")
        v = build_verdict(["ht_trig", "benign_xor"], [], [], trigger_evidence=ev)
        assert v.verdict == "trojan_suspected"
        by = {s.gate: s for s in v.suspects}
        assert by["tg_clk"].confidence >= 0.9
        # Shape adds to trigger evidence; shape alone is dropped once the
        # trigger detector has found the Trojan (it only dilutes the list).
        assert by["ht_trig"].confidence > 0.6
        assert "benign_xor" not in by

    def test_shape_only_leads_stay_when_nothing_strong_was_found(self):
        from pyatpg.atpg.trigger_detector import TriggerEvidence
        from pyatpg.atpg.verdict import build_verdict

        ev = TriggerEvidence()
        ev.add("lead", "never_toggling_cluster", "one of 6 never-toggling gates")
        v = build_verdict(["benign_xor"], [], [], trigger_evidence=ev)
        assert v.verdict == "inconclusive"
        assert {s.gate for s in v.suspects} == {"lead", "benign_xor"}
        assert "1 with a single trigger-like sign" in v.summary
        assert "1 matching the XOR-splice shape" in v.summary


class TestGateTypeCoverage:
    """Every gate type the parser accepts must be handled by the simulator and
    SCOAP, or those gates silently corrupt the features derived from them."""

    def test_buff_is_simulated_like_buf(self):
        """BUFF is the ISCAS .bench spelling and is common in these netlists.
        Unhandled, it evaluated to a constant 0, never toggled, and fell back
        to the default 0.5 toggle rate."""
        src = """
module b(a, out1, out2);
input a;
output out1, out2;
wire w1, w2;
buf  g1(w1, a);
buff g2(w2, a);
not  g3(out1, w1);
not  g4(out2, w2);
endmodule
"""
        p = _profile(src, toggle_patterns=400)
        feats = {str(g["gate_id"]): g["features"] for g in p.gate_profiles}
        assert feats["w1"]["toggle_rate"] == feats["w2"]["toggle_rate"]
        # A buffer of a random input toggles; it must not read as static.
        assert feats["w2"]["toggle_rate"] > 0.0

    def test_every_valid_gate_type_is_evaluated(self):
        from pyatpg.core.netlist import Circuit
        from pyatpg.core.graph_analysis import CircuitGraph

        for gtype in sorted(Circuit.VALID_GATES):
            arity = 1 if gtype in ("NOT", "BUF", "BUFF") else 2
            ins = ", ".join("i%d" % i for i in range(arity))
            src = (
                "module t(%s, out);\ninput %s;\noutput out;\n%s g(out, %s);\nendmodule"
                % (ins, ins, gtype.lower(), ins)
            )
            p = _profile(src, toggle_patterns=200)
            feats = {str(g["gate_id"]): g["features"] for g in p.gate_profiles}
            rate = feats["out"]["toggle_rate"]
            # 0.5 is the "no data" default; a driven output must have real data.
            assert rate != 0.5, f"{gtype} produced no simulated transitions"


def _chain_netlist(n: int) -> str:
    """A NAND chain of ``n`` gates, each with its own side input."""
    lines = [
        "module big(" + ", ".join(f"i{k}" for k in range(n + 1)) + ", out);",
        "input " + ", ".join(f"i{k}" for k in range(n + 1)) + ";",
        "output out;",
        "wire " + ", ".join(f"w{k}" for k in range(n)) + ";",
        "nand g0(w0, i0, i1);",
    ]
    lines += [f"nand g{k}(w{k}, w{k - 1}, i{k + 1});" for k in range(1, n)]
    lines += [f"buf gout(out, w{n - 1});", "endmodule"]
    return "\n".join(lines)


class TestPromptBudget:
    def test_many_forced_suspects_cannot_stall_the_prompt_shrink(self, monkeypatch):
        """An uncapped forced list kept every pool over budget, so the shrink
        loop never ended (an s38417 run sat in gate selection for an hour)."""
        import time

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "1500")
        p = _profile(_chain_netlist(300), toggle_patterns=200)

        class Fake:
            model_name, label, last_usage = "fake", "Fake", None

            def complete(self, prompt, **kw):
                return "g0,0.9"

        extra = [(str(g["gate_id"]), "flagged") for g in p.gate_profiles]
        start = time.monotonic()
        sel = FaultTargetSelector(provider=Fake(), context_gate_limit=200).select(
            p.gate_profiles, graph=p.graph, extra_suspects=extra
        )
        assert time.monotonic() - start < 20
        assert sel.context["gates_sent"] <= sel.context["gate_limit"]
        # Every flagged gate still reaches the fault list.
        assert {g for g, _ in extra} <= set(sel.gates)


class TestBatchedSelection:
    """A circuit that overflows one request is reviewed in batches."""

    @staticmethod
    def _recording():
        prompts = []

        class Rec:
            model_name, label, last_usage = "fake", "Fake", None

            def complete(self, prompt, **kw):
                prompts.append(prompt)
                return "g0,0.9\ng1,0.8\ng2,0.7"

        return Rec(), prompts

    def test_overflowing_pool_is_scored_in_independent_batches(self, monkeypatch):
        from pyatpg.atpg import targeting as T

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "4000")
        monkeypatch.setenv("PYATPG_LLM_BATCHES", "3")
        provider = _scoring_provider(lambda a: 50)
        profiles = TestRequestTokenBudget._big_profiles()
        sel = T.FaultTargetSelector(provider=provider).select(profiles)
        assert len(provider.prompts) == 3 == sel.context["batches"]
        assert "shortlist" not in provider.prompts[1]
        # Every gate shown was scored, none twice, and more of the circuit
        # was seen than one request could carry.
        pool = sel.context["pool"]
        assert len(pool) == len(set(pool)) and set(sel.context["scores"]) == set(pool)
        rows = len(re.findall(r"^g\d+,", provider.prompts[0], flags=re.MULTILINE))
        assert len(pool) > rows

    def test_one_batch_setting_sends_a_single_request(self, monkeypatch):
        from pyatpg.atpg import targeting as T

        monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "4000")
        monkeypatch.setenv("PYATPG_LLM_BATCHES", "1")
        provider = _scoring_provider(lambda a: 50)
        T.FaultTargetSelector(provider=provider).select(TestRequestTokenBudget._big_profiles())
        assert len(provider.prompts) == 1


class TestDetectorSwitches:
    def test_shape_matches_stay_out_of_the_list_when_not_forced(self):
        p = _profile(TROJAN, toggle_patterns=600)
        sel = FaultTargetSelector(api_key=None, max_gates=1).select(
            p.gate_profiles, graph=p.graph, force_shape=False
        )
        assert sel.structural_suspects  # still reported
        assert not set(sel.structural_suspects) <= set(sel.gates)

    def test_detector_off_forces_nothing(self):
        p = _profile(TROJAN, toggle_patterns=600)
        sel = FaultTargetSelector(api_key=None, max_gates=1).select(
            p.gate_profiles, graph=p.graph, use_detector=False,
            extra_suspects=[(str(p.gate_profiles[0]["gate_id"]), "flagged")],
        )
        assert sel.structural_suspects == [] and len(sel.gates) == 1


def test_analysis_regions_take_the_models_slots_first(monkeypatch):
    from pyatpg.atpg.circuit_analyst import CircuitAnalysis, SuspiciousRegion
    from pyatpg.atpg.targeting import analysis_region_gates

    profiles = TestRequestTokenBudget._big_profiles()
    narrow = [str(p["gate_id"]) for p in profiles[-3:]]  # the least rare: never in the floor
    analysis = CircuitAnalysis(source="llm", suspicious_regions=[
        SuspiciousRegion(name="narrow", gates=narrow),
        SuspiciousRegion(name="whole pool", gates=[f"x{k}" for k in range(120)]),
    ])
    hints = analysis_region_gates(analysis)
    assert hints == narrow  # the 120-gate "region" says nothing

    monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "0")
    sel = FaultTargetSelector(provider=_scoring_provider(lambda a: 0)).select(
        profiles, hints=hints)
    assert sel.context["llm_picks"][:3] == narrow
    assert sel.context["analysis_hints"] == narrow
