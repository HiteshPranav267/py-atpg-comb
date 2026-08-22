"""Targeting: reproducible ranking, structural trojan detection, and the
guarantee that a detected suspect always reaches the fault list."""
import pytest

from pyatpg.atpg.structural import parse_circuit, profile_circuit
from pyatpg.atpg.suspects import find_structural_suspects
from pyatpg.atpg.targeting import FaultTargetSelector
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

        captured = {}

        class FakeModels:
            def generate_content(self, model, contents):
                captured["prompt"] = contents

                class R:
                    text = "g0,g1,g2"

                return R()

        class FakeClient:
            def __init__(self, api_key=None):
                self.models = FakeModels()

        original = T.genai
        T.genai = type("X", (), {"Client": FakeClient})
        try:
            selection = T.FaultTargetSelector(api_key="fake").select(profiles)
        finally:
            T.genai = original
        return captured["prompt"], selection

    def test_real_gate_names_never_reach_the_model(self):
        p = _profile(TROJAN, toggle_patterns=200)
        prompt, _ = self._capture_prompt(p.gate_profiles)
        for name in ("ht_trig", "n5_orig", "n1", "n2"):
            assert f"Gate: {name}," not in prompt
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

        p = _profile(TROJAN, toggle_patterns=200)

        class FakeModels:
            def generate_content(self, model, contents):
                class R:
                    text = "g0, g99999, not_an_alias, ht_trig"

                return R()

        class FakeClient:
            def __init__(self, api_key=None):
                self.models = FakeModels()

        original = T.genai
        T.genai = type("X", (), {"Client": FakeClient})
        try:
            selection = T.FaultTargetSelector(api_key="fake").select(p.gate_profiles)
        finally:
            T.genai = original
        # Only the one real alias survives; a raw gate name is not an alias.
        assert len(selection.gates) == 1


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
