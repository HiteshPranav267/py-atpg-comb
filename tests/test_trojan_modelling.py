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


def test_trigger_detector_flags_logic_clock_and_payload():
    from pyatpg.atpg.trigger_detector import static_evidence

    circuit, _ = _parse(T100_LIKE)
    comb, _ = to_full_scan(circuit)
    assert comb.logic_clocks == {"Tg1": "Tg1_Trigger1"}
    ev = static_evidence(comb, comb.logic_clocks)
    kinds = {g: [k for k, _ in e] for g, e in ev.by_gate.items()}
    assert "logic_clock" in kinds["Tg1_Trigger1"]
    assert "logic_clock_payload" in kinds["y"]
    assert "q_out" not in kinds and "r0" not in kinds


def test_never_toggling_cluster_is_found():
    from pyatpg.atpg.trigger_detector import MIN_CLUSTER, static_evidence

    # A 10-level chain of 4-input ANDs, three fresh inputs per level: from
    # level 4 on (probability <= 2**-16) it is never 1 in 16384 random patterns.
    lines = ["module t(" + ", ".join(f"i{k}" for k in range(40)) + ", y);",
             "input " + ", ".join(f"i{k}" for k in range(40)) + ";", "output y;"]
    prev = None
    for lvl in range(10):
        ins = [f"i{4 * lvl + j}" for j in range(4)] if prev is None else [prev] + [f"i{4 * lvl + j}" for j in range(3)]
        lines.append(f"AND4X1 a{lvl} (.IN1({ins[0]}), .IN2({ins[1]}), .IN3({ins[2]}), .IN4({ins[3]}), .Q(t{lvl}));")
        prev = f"t{lvl}"
    lines += ["NBUFFX2 o (.IN(t9), .Q(y));", "endmodule"]
    circuit, _ = _parse("\n".join(lines))
    ev = static_evidence(circuit)
    flagged = [g for g, e in ev.by_gate.items() if any(k == "never_toggling_cluster" for k, _ in e)]
    assert len(flagged) >= MIN_CLUSTER


def test_scan_enable_is_held_low_under_full_scan():
    circuit, _ = _parse("""
module t (CK, a, test_se, test_si, y);
  input CK, a, test_se, test_si;
  output y;
  SDFFX1 r (.D(a), .SI(test_si), .SE(test_se), .CLK(CK), .Q(q));
  INVX0 n (.IN(test_se), .QN(se_n));
  AND2X1 g (.IN1(q), .IN2(se_n), .Q(y));
endmodule
""")
    comb, counts = to_full_scan(circuit)
    assert counts["scan_enables_held_low"] == ["test_se"]
    assert "test_se" not in comb.inputs
    assert comb.gates["y"].gtype == "BUF" and comb.gates["y"].inputs == ["q"]


def test_rate_limit_retry_waits_as_long_as_the_provider_asks():
    from pyatpg.ranking.providers import _retry_delay

    assert _retry_delay("Rate limit ... Please try again in 12.855s. Need more", 0) == 13.855
    assert _retry_delay("try again in 450ms", 0) == 1.45
    assert _retry_delay("try again in 5m0s", 0) == 65.0  # capped
    assert _retry_delay("503 UNAVAILABLE", 2) == 4.0


def _bench(lines):
    from pyatpg.atpg.structural import parse_circuit

    return parse_circuit("\n".join(lines) + "\n", "t.bench").circuit


def test_payload_splice_in_front_of_an_output_is_found():
    """s38584-T100's shape: OR(trigger tree, rare original signal) -> output."""
    from pyatpg.atpg.trigger_detector import static_evidence

    lines = [f"INPUT(i{k})" for k in range(14)] + ["OUTPUT(y)"]
    # Original logic: a rare signal (1 in 64 random patterns).
    lines += ["d1 = AND(i0, i1, i2)", "d2 = AND(i3, i4, i5)", "data = AND(d1, d2)"]
    # Inserted trigger: a fanout-free tree over other inputs, common under scan.
    lines += ["t1 = NOT(i6)", "t2 = NOT(i7)", "t3 = AND(t1, t2)", "t4 = NOT(i8)",
              "t5 = NOT(i9)", "t6 = AND(t4, t5)", "trig = OR(t3, t6)"]
    lines += ["y = OR(trig, data)"]
    ev = static_evidence(_bench(lines))
    kinds = {g: [k for k, _ in e] for g, e in ev.by_gate.items()}
    assert "payload_splice" in kinds["y"]
    assert "payload_splice" in kinds["trig"] and "payload_splice" in kinds["t1"]
    assert "data" not in kinds and "d1" not in kinds


def test_ordinary_output_gate_is_not_a_splice():
    from pyatpg.atpg.trigger_detector import static_evidence

    lines = [f"INPUT(i{k})" for k in range(8)] + ["OUTPUT(y)"]
    lines += ["a1 = AND(i0, i1)", "a2 = AND(i2, i3)", "a = OR(a1, a2)",
              "b1 = AND(i4, i5)", "b2 = AND(i6, i7)", "b = OR(b1, b2)", "y = AND(a, b)"]
    ev = static_evidence(_bench(lines))
    assert not any(k == "payload_splice" for e in ev.by_gate.values() for k, _ in e)


def test_duplicate_gate_input_is_a_lead():
    from pyatpg.atpg.trigger_detector import static_evidence

    lines = ["INPUT(a)", "INPUT(b)", "INPUT(c)", "OUTPUT(y)",
             "n1 = AND(a, b)", "y = NOR(n1, n1, c)"]
    ev = static_evidence(_bench(lines))
    assert [k for k, _ in ev.by_gate["y"]] == ["duplicate_input"]
    assert ev.strength("y") < 0.7  # a lead on its own, not a verdict


def test_correlated_evidence_does_not_add_up():
    """Never-toggling nets are also never activated: one mechanism, not two."""
    from pyatpg.atpg.trigger_detector import TriggerEvidence

    ev = TriggerEvidence()
    ev.add("g", "never_toggling_cluster", "x")
    ev.add("g", "unactivated_cluster", "y")
    assert ev.strength("g") < 0.7
    ev.add("g", "duplicate_input", "z")  # an independent mechanism does add
    assert ev.strength("g") >= 0.7


def test_never_fires_needs_the_trigger_net_itself():
    """s38584-T300: a redundant branch inside module TrojanTrigger is not the trigger."""
    from pyatpg.atpg.service import _is_trigger_output_fault

    assert _is_trigger_output_fault("Tg1_Trigger1 /0")
    assert not _is_trigger_output_fault("Tg1_Trigger1 /1")
    assert not _is_trigger_output_fault("TrojanTrigger__Clock_counter[31]->TrojanTrigger__N319 /0")
    assert not _is_trigger_output_fault("TrojanTrigger__N319 /0")


def _counter(enable: bool) -> str:
    # A 4-bit ripple incrementer; with ``enable`` it counts only when input en is 1.
    lines = ["module t (CK, en, a, y);", "  input CK, en, a;", "  output y;"]
    carry = "en" if enable else None
    for k in range(4):
        if carry is None:
            lines.append(f"  INVX0 s{k} (.IN(c{k}), .QN(d{k}));")
            carry = f"c{k}"
        else:
            lines.append(f"  XOR2X1 s{k} (.IN1(c{k}), .IN2({carry}), .Q(d{k}));")
            lines.append(f"  AND2X1 k{k} (.IN1(c{k}), .IN2({carry}), .Q(k{k}_o));")
            carry = f"k{k}_o"
        lines.append(f"  DFFX1 r{k} (.D(d{k}), .CLK(CK), .Q(c{k}));")
    lines += ["  AND2X1 g (.IN1(a), .IN2(c3), .Q(y));", "endmodule"]
    return "\n".join(lines)


def test_free_running_timer_is_a_lead():
    from pyatpg.atpg.trigger_detector import static_evidence

    comb, _ = to_full_scan(_parse(_counter(enable=False))[0])
    assert set(comb.scan_cells) == {"c0", "c1", "c2", "c3"}
    ev = static_evidence(comb, comb.logic_clocks)
    for net in ("c0", "c3", "d0", "d3"):
        assert [k for k, _ in ev.by_gate[net]] == ["autonomous_timer"]
    assert ev.strength("c0") < 0.7  # benign designs have counters too
    assert "y" not in ev.by_gate


def test_counter_that_reads_an_input_is_not_a_timer():
    from pyatpg.atpg.trigger_detector import static_evidence

    comb, _ = to_full_scan(_parse(_counter(enable=True))[0])
    ev = static_evidence(comb, comb.logic_clocks)
    assert not any("timer" in k for e in ev.by_gate.values() for k, _ in e)


def test_payload_mux_selected_by_the_trigger_is_flagged_whole():
    from pyatpg.atpg.trigger_detector import static_evidence

    circuit, _ = _parse("""
module top (CK, a, b, c, d, e, f, y);
  input CK, a, b, c, d, e, f;
  output y;
  AND4X1 Tg1_Tj_Trigger (.IN1(a), .IN2(b), .IN3(c), .IN4(d), .Q(Tg1_Trigger1));
  DFFNX2 Tg1_Trigger (.CLK(Tg1_Trigger1), .D(1'b1), .Q(Tg1));
  NBUFFX2 s1 (.IN(Tg1), .Q(s2));
  NBUFFX2 s3 (.IN(s2), .Q(s4));
  NBUFFX2 s5 (.IN(s4), .Q(sel));
  MUX21X2 Trojan_Payload (.IN1(e), .IN2(f), .S(sel), .Q(y));
endmodule
""")
    comb, _ = to_full_scan(circuit)
    ev = static_evidence(comb, comb.logic_clocks)
    mux = {g for g, e in ev.by_gate.items() if any(k == "payload_mux" for k, _ in e)}
    assert "y" in mux and len(mux) == 4  # NOT(sel), both AND legs, the OR
    # The register's own load mux (select = its clock) is state, not payload.
    assert not any(g.startswith("Tg1__") for g in mux)
