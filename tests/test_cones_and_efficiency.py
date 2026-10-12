"""Cone view for the LLM, and the standard-vs-targeted comparison."""
import re

from pyatpg.atpg.cones import CONE_HEADER, cone_line, expansion, find_cones
from pyatpg.atpg.efficiency import _greedy_cover, trojan_fault_detection
from pyatpg.atpg.faultsim import _levelize, detection_masks
from pyatpg.atpg.models import TestVector, TestVectorSet
from pyatpg.atpg.structural import parse_circuit, profile_circuit
from pyatpg.atpg.targeting import FaultTargetSelector
from pyatpg.atpg.trojan_metrics import _random_probabilities


def _bench(lines):
    return parse_circuit("\n".join(lines) + "\n", "t.bench")


def _host_with_trojan():
    """A host with ordinary logic and an 11-gate AND-tree trigger driving an XOR payload."""
    lines = [f"INPUT(i{k})" for k in range(40)] + ["OUTPUT(y)", "OUTPUT(z)", "OUTPUT(w)"]
    lines += ["h1 = AND(i30, i31)", "h2 = OR(i32, i33)", "z = AND(h1, h2)", "w = NOR(i34, i35)"]
    for k in range(8):
        lines.append(f"Tj_OUT{k + 1} = AND(i{2 * k}, i{2 * k + 1})")
    lines += ["Tj_OUT1234 = AND(Tj_OUT1, Tj_OUT2, Tj_OUT3, Tj_OUT4)",
              "Tj_OUT5678 = AND(Tj_OUT5, Tj_OUT6, Tj_OUT7, Tj_OUT8)",
              "Tj_Trigger = AND(Tj_OUT1234, Tj_OUT5678)",
              "y = XOR(i36, Tj_Trigger)"]
    return _bench(lines)


def test_the_trigger_tree_is_one_cone_with_its_payload():
    circuit = _host_with_trojan().circuit
    probs = _random_probabilities(circuit, _levelize(circuit), 4096, 2024)
    cones = find_cones(circuit, probs)
    trojan = next(c for c in cones if c.root == "Tj_Trigger")
    assert len(trojan.gates) == 11 and trojan.depth == 2
    assert "y" in expansion(trojan)
    # Its sub-trees are part of it, not cones of their own.
    assert not any(c.root in ("Tj_OUT1234", "Tj_OUT5678") for c in cones)
    line = cone_line("c0", trojan)
    assert line.count(",") == CONE_HEADER.count(",")
    assert "Tj_" not in line  # names never reach the model


def _vectors(circuit, rows):
    pis = sorted(circuit.inputs)
    return TestVectorSet(primary_inputs=pis, primary_outputs=[], vectors=[
        TestVector(index=i, inputs="".join("1" if ones.get(p) else "0" for p in pis), outputs="")
        for i, ones in enumerate(rows)])


def test_detection_masks_mark_the_patterns_that_detect_each_fault():
    circuit = _bench(["INPUT(a)", "INPUT(b)", "OUTPUT(y)", "y = AND(a, b)"]).circuit
    vs = _vectors(circuit, [{}, {"a": 1, "b": 1}, {"a": 1}])
    m = detection_masks(circuit, vs, ["y", "a"])
    assert m[("y", 0)] == 0b010           # only pattern 1 drives y to 1
    assert m[("y", 1)] == 0b101
    assert m[("a", 0)] == 0b010           # a stuck-at-0 shows when b = 1 and a = 1


def test_trojan_detection_counts_patterns_to_detect_everything():
    circuit = _bench(["INPUT(a)", "INPUT(b)", "INPUT(c)", "OUTPUT(y)", "Tj_t = AND(a, b)",
                      "y = XOR(Tj_t, c)"]).circuit
    vs = _vectors(circuit, [{"c": 1}, {}, {"a": 1}, {"a": 1, "b": 1}])
    r = trojan_fault_detection(circuit, vs, ["Tj_t"])
    assert r["trojan_faults"] == 2 and r["detected"] == 2
    assert r["patterns_to_detect_all"] == 4  # the stuck-at-0 needs pattern 3
    assert r["min_patterns_oracle"] == 2


def test_greedy_cover():
    assert _greedy_cover([0b0011, 0b0110, 0b1000], 4) == 2
    assert _greedy_cover([], 4) == 0


class _Scorer:
    model_name, label, last_usage = "fake", "Fake", None

    def __init__(self, favour):
        self.favour, self.prompts = favour, []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        rows = re.findall(r"^(c\d+),(\d+),", prompt, flags=re.MULTILINE)
        return "\n".join(f"{a},{90 if self.favour(int(n)) else 5}" for a, n in rows)


def test_cone_view_lists_match_in_size_and_differ_only_in_the_cones_chosen(monkeypatch):
    monkeypatch.setenv("LLM_REQUEST_TOKEN_BUDGET", "0")
    parsed = _host_with_trojan()
    profiles = profile_circuit(parsed, toggle_patterns=400).gate_profiles
    llm = FaultTargetSelector(provider=_Scorer(lambda n: n == 11), max_gates=12).select(
        profiles, circuit=parsed.circuit)
    base = FaultTargetSelector(provider=None, max_gates=12).select(
        profiles, circuit=parsed.circuit)
    assert llm.context["view"] == "cones" and base.context["view"] == "cones"
    assert len(llm.gates) == len(base.gates) == 12
    floor = llm.context["floor"]
    assert llm.gates[:floor] == base.gates[:floor]
    # The model's top cone (the 11-gate trigger) goes first, then the rarest
    # cone, in turn.
    used = llm.context["cones_used"]
    assert used[0] == {"root": "Tj_Trigger", "from": "llm"}
    assert all(u["from"] == ("llm" if i % 2 == 0 else "rarity") for i, u in enumerate(used))
    assert {f"Tj_OUT{k}" for k in range(1, 9)} <= set(llm.gates)


def test_the_rarest_cone_keeps_its_place_when_the_model_prefers_another(monkeypatch):
    from pyatpg.atpg.targeting import _alternate

    class C:
        def __init__(self, root):
            self.root = root

    rarest = [C("r1"), C("r2"), C("x")]
    model = [C("x"), C("m2")]
    assert [(c.root, s) for c, s in _alternate(model, rarest)] == [
        ("x", "llm"), ("r1", "rarity"), ("m2", "llm"), ("r2", "rarity")]


def test_gate_view_setting_turns_cones_off(monkeypatch):
    monkeypatch.setenv("PYATPG_LLM_VIEW", "gates")
    parsed = _host_with_trojan()
    profiles = profile_circuit(parsed, toggle_patterns=400).gate_profiles
    sel = FaultTargetSelector(provider=None, max_gates=12).select(profiles, circuit=parsed.circuit)
    assert sel.context.get("view") != "cones"


def test_text_settings_are_accepted():
    from pyatpg.core import settings

    try:
        assert settings.set_override("llm_view", " Gates ") == "gates"
    finally:
        settings.set_override("llm_view", None)
