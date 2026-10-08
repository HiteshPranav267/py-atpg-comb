"""Logic-clocked registers (Trojan triggers), constant tie-offs, and Trojan metrics."""

from pyatpg.atpg.models import TestVector as Vector, TestVectorSet as VectorSet
from pyatpg.atpg.trojan_metrics import ground_truth_gates, rare_node_activation, score_selection
from pyatpg.core.scan import to_full_scan
from pyatpg.core.verilog_parser import VerilogParser

# A miniature of Trust-Hub s15850-T100: a trigger register clocked by a rare
# AND condition (D tied to 1), a second one shifting it on, and a payload MUX.
T100_LIKE = """
module top (CK, a, b, c, d, y, q_out);
  input CK, a, b, c, d;
  output y, q_out;
  DFFX1 r0 (.D(a), .CLK(CK), .Q(q_out));
  AND4X1 Tg1_Tj_Trigger (.IN1(a), .IN2(b), .IN3(c), .IN4(d), .Q(Tg1_Trigger1));
  DFFNX2 Tg1_Trigger (.CLK(Tg1_Trigger1), .D(1'b1), .Q(Tg1));
  MUX21X2 Trojan_Payload (.IN1(b), .IN2(c), .S(Tg1), .Q(y));
endmodule
"""


def _parse(text):
    p = VerilogParser()
    return p.parse(text, "t.v"), p.notes


def test_register_on_the_clock_input_is_unchanged():
    circuit, _ = _parse(T100_LIKE)
    r0 = next(ff for ff in circuit.flipflops if ff.q == "q_out")
    assert r0.d == "a"


def test_logic_clocked_register_loads_only_when_its_clock_net_is_high():
    circuit, notes = _parse(T100_LIKE)
    tg1 = next(ff for ff in circuit.flipflops if ff.q == "Tg1")
    assert tg1.d != "1'b1" and tg1.d in circuit.gates  # a load mux, not the constant
    assert any("clocked by logic" in n for n in notes)
    # The trigger logic now drives something and survives into full scan.
    comb, _ = to_full_scan(circuit)
    assert "Tg1_Trigger1" in comb.gates
    assert not any("'" in n for n in set(comb.gates) | set(comb.inputs) | set(comb.outputs))


def test_constant_register_input_gets_a_named_tie_off_and_no_pseudo_output():
    circuit, _ = _parse("""
module t (CK, a, y);
  input CK, a;
  output y;
  DFFX1 r (.D(1'b1), .CLK(CK), .Q(q));
  AND2X1 g (.IN1(a), .IN2(q), .Q(y));
endmodule
""")
    r = circuit.flipflops[0]
    assert r.d == "__const1"
    comb, counts = to_full_scan(circuit)
    assert counts["constant_d_flip_flops"] == 1
    assert "__const1" not in comb.outputs and "__tie_src" not in comb.inputs


def test_ground_truth_and_selection_scoring():
    circuit, _ = _parse(T100_LIKE)
    truth = ground_truth_gates(circuit)
    assert "Tg1_Trigger1" in truth and "Tg1" in truth
    score = score_selection(["Tg1_Trigger1", "a_gate"], truth)
    assert score["precision"] == 0.5 and score["trojan_gates_hit"] == ["Tg1_Trigger1"]


def test_rare_node_activation_counts_patterns_reaching_the_rare_value():
    circuit, _ = _parse(T100_LIKE)
    comb, _ = to_full_scan(circuit)
    pis = sorted(comb.inputs)
    def vec(i, **ones):
        return Vector(index=i, inputs="".join("1" if ones.get(p) else "0" for p in pis), outputs="")
    never = VectorSet(primary_inputs=pis, primary_outputs=[], vectors=[vec(1), vec(2, a=1)])
    fires = VectorSet(primary_inputs=pis, primary_outputs=[],
                          vectors=[vec(1, a=1, b=1, c=1, d=1)])
    # A 4-input AND is 1 in 1/16 of random patterns: rare at a 10% threshold.
    r_never = rare_node_activation(comb, never, threshold=0.1, watch=["Tg1_Trigger1"])
    r_fires = rare_node_activation(comb, fires, threshold=0.1, watch=["Tg1_Trigger1"])
    trig = lambda r: next(w for w in r["watched"] if w["net"] == "Tg1_Trigger1")  # noqa: E731
    assert trig(r_never)["patterns_activating"] == 0
    assert trig(r_fires)["patterns_activating"] == 1
    assert any(n["net"] == "Tg1_Trigger1" for n in r_never["never_activated"])


def test_atalanta_gets_a_fixed_seed(monkeypatch):
    from pyatpg.atpg import runner

    assert runner._seed() == 1
    monkeypatch.setenv("PYATPG_ATALANTA_SEED", "0")
    assert runner._seed() == 0
