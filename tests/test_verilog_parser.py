"""Structural Verilog parser: cell lowering, hierarchy, buses and constants."""

import itertools

import pytest

from pyatpg.atpg.structural import parse_circuit
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.seq.timeframe import is_combinational_part_acyclic


def _eval(circuit, values):
    """Evaluate every gate given primary-input (and flip-flop Q) values."""
    vals = dict(values)
    pending = dict(circuit.gates)
    while pending:
        progressed = False
        for out, g in list(pending.items()):
            if all(i in vals for i in g.inputs):
                ins = [vals[i] for i in g.inputs]
                t = g.gtype
                if t == "AND":
                    v = int(all(ins))
                elif t == "NAND":
                    v = int(not all(ins))
                elif t == "OR":
                    v = int(any(ins))
                elif t == "NOR":
                    v = int(not any(ins))
                elif t == "XOR":
                    v = sum(ins) % 2
                elif t == "NOT":
                    v = 1 - ins[0]
                else:
                    v = ins[0]
                vals[out] = v
                del pending[out]
                progressed = True
        assert progressed, "combinational loop"
    return vals


def _cell_netlist(instance, inputs, outputs):
    ports = ", ".join(inputs + outputs)
    return (
        f"module top ({ports});\n"
        f"  input {', '.join(inputs)};\n"
        f"  output {', '.join(outputs)};\n"
        f"  {instance}\n"
        f"endmodule\n"
    )


CELLS = [
    # (instance, inputs, outputs, reference function over the input tuple)
    ("NAND2X0 U ( .IN1(a), .IN2(b), .QN(y) );", ["a", "b"], ["y"],
     lambda a, b: {"y": 1 - (a & b)}),
    ("XNOR2X1 U ( .IN1(a), .IN2(b), .Q(y) );", ["a", "b"], ["y"],
     lambda a, b: {"y": 1 - (a ^ b)}),
    ("XNOR3X1 U ( .IN1(a), .IN2(b), .IN3(c), .Q(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": 1 - (a ^ b ^ c)}),
    ("XOR3X1 U ( .IN1(a), .IN2(b), .IN3(c), .Q(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": a ^ b ^ c}),
    ("AO22X1 U ( .IN1(a), .IN2(b), .IN3(c), .IN4(d), .Q(y) );", ["a", "b", "c", "d"], ["y"],
     lambda a, b, c, d: {"y": (a & b) | (c & d)}),
    ("AO221X1 U ( .IN1(a), .IN2(b), .IN3(c), .IN4(d), .IN5(e), .Q(y) );",
     ["a", "b", "c", "d", "e"], ["y"],
     lambda a, b, c, d, e: {"y": (a & b) | (c & d) | e}),
    ("AOI222X1 U ( .IN1(a), .IN2(b), .IN3(c), .IN4(d), .IN5(e), .IN6(f), .QN(y) );",
     ["a", "b", "c", "d", "e", "f"], ["y"],
     lambda a, b, c, d, e, f: {"y": 1 - ((a & b) | (c & d) | (e & f))}),
    ("OA21X1 U ( .IN1(a), .IN2(b), .IN3(c), .Q(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": (a | b) & c}),
    ("OAI22X1 U ( .IN1(a), .IN2(b), .IN3(c), .IN4(d), .QN(y) );", ["a", "b", "c", "d"], ["y"],
     lambda a, b, c, d: {"y": 1 - ((a | b) & (c | d))}),
    # GSCLib / Nangate pin lettering: the letter names the group.
    ("AOI21X1 U ( .A0(a), .A1(b), .B0(c), .Y(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": 1 - ((a & b) | c)}),
    ("OAI21_X1 U ( .A(c), .B1(a), .B2(b), .ZN(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": 1 - ((a | b) & c)}),
    ("MUX21X1 U ( .IN1(a), .IN2(b), .S(s), .Q(y) );", ["a", "b", "s"], ["y"],
     lambda a, b, s: {"y": b if s else a}),
    ("MX2X1 U ( .A(a), .B(b), .S0(s), .Y(y) );", ["a", "b", "s"], ["y"],
     lambda a, b, s: {"y": b if s else a}),
    ("HADDX1 U ( .A0(a), .B0(b), .C1(co), .SO(s) );", ["a", "b"], ["co", "s"],
     lambda a, b: {"co": a & b, "s": a ^ b}),
    ("FADDX1 U ( .A(a), .B(b), .CI(c), .CO(co), .S(s) );", ["a", "b", "c"], ["co", "s"],
     lambda a, b, c: {"co": int(a + b + c >= 2), "s": (a + b + c) % 2}),
    ("ISOLANDX1 U ( .D(a), .ISO(b), .Q(y) );", ["a", "b"], ["y"],
     lambda a, b: {"y": a & b}),
    ("NBUFFX2 U ( .INP(a), .Z(y) );", ["a"], ["y"], lambda a: {"y": a}),
    ("INVX0 U ( .INP(a), .ZN(y) );", ["a"], ["y"], lambda a: {"y": 1 - a}),
    ("NAND3X1 U ( .A(a), .B(b), .C(c), .Y(y) );", ["a", "b", "c"], ["y"],
     lambda a, b, c: {"y": 1 - (a & b & c)}),
]


@pytest.mark.parametrize("instance,inputs,outputs,ref", CELLS, ids=[c[0].split()[0] for c in CELLS])
def test_library_cell_lowering_is_exact(instance, inputs, outputs, ref):
    circuit = VerilogParser().parse(_cell_netlist(instance, inputs, outputs))
    assert circuit.flipflops == []
    for combo in itertools.product((0, 1), repeat=len(inputs)):
        vals = _eval(circuit, dict(zip(inputs, combo)))
        expected = ref(*combo)
        assert {o: vals[o] for o in outputs} == expected, combo


def test_unknown_cell_is_an_error_not_a_buffer():
    src = _cell_netlist("WEIRDX1 U ( .A(a), .Y(y) );", ["a"], ["y"])
    with pytest.raises(ValueError, match="WEIRDX1"):
        VerilogParser().parse(src)


def test_statement_split_across_lines_keeps_output_pin():
    # ``.Q(y)`` ends a line before the instance's closing ``);`` -- the old
    # line joiner cut the statement there and lost the output.
    src = (
        "module top (a, b, c, d, y);\n  input a, b, c, d;\n  output y;\n"
        "  AO22X1 U1 ( .IN1(a), .IN2(b), .IN3(c), .IN4(d), .Q(y)\n        );\n"
        "endmodule\n"
    )
    circuit = VerilogParser().parse(src)
    assert "y" in circuit.gates


def test_register_cells_with_qn_and_async_reset():
    src = """
module top (CK, a, rst, se, si, y, z);
  input CK, a, rst, se, si;
  output y, z;
  wire n1, qn2;
  DFFARX1 r1 ( .D(a), .CLK(CK), .RSTB(rst), .Q(y), .QN(n1) );
  SDFFX1 r2 ( .D(n1), .SI(si), .SE(se), .CLK(CK), .QN(qn2) );
  INVX0 U1 ( .INP(qn2), .ZN(z) );
endmodule
"""
    circuit = VerilogParser().parse(src)
    ffs = {ff.d: ff for ff in circuit.flipflops}
    assert set(ffs) == {"a", "n1"}
    assert not ffs["a"].is_scan and ffs["a"].q == "y"
    assert ffs["n1"].is_scan and (ffs["n1"].si, ffs["n1"].se) == ("si", "se")
    assert circuit.gates["n1"].gtype == "NOT" and circuit.gates["n1"].inputs == ["y"]
    assert circuit.gates["qn2"].inputs == [ffs["n1"].q]
    assert "rst" not in {i for g in circuit.gates.values() for i in g.inputs}


def test_hierarchy_and_buses_are_flattened():
    src = """
module inc2 ( A, SUM );
  input [1:0] A;
  output [1:0] SUM;
  HADDX1 U0 ( .A0(A[0]), .B0(1'b1), .C1(c), .SO(SUM[0]) );
  XOR2X1 U1 ( .IN1(A[1]), .IN2(c), .Q(SUM[1]) );
endmodule

module top ( x1, x0, s1, s0 );
  input x1, x0;
  output s1, s0;
  inc2 add ( .A({x1, x0}), .SUM({s1, s0}) );
endmodule
"""
    circuit = VerilogParser().parse(src)
    assert circuit.inputs == {"x0", "x1"} and circuit.outputs == {"s0", "s1"}
    assert any(g.startswith("add__") for g in circuit.gates)
    for x1, x0 in itertools.product((0, 1), repeat=2):
        vals = _eval(circuit, {"x1": x1, "x0": x0})
        total = (2 * x1 + x0 + 1) % 4
        assert (vals["s1"], vals["s0"]) == (total >> 1, total & 1)


def test_constants_fold_and_tied_outputs_are_dropped():
    src = """
module top ( a, b, y, t );
  input a, b;
  output y, t;
  wire n1;
  assign t = 1'b1;
  NOR2X0 U1 ( .IN1(a), .IN2(1'b0), .QN(n1) );
  AO22X1 U2 ( .IN1(n1), .IN2(b), .IN3(1'b0), .IN4(a), .Q(y) );
endmodule
"""
    parser = VerilogParser()
    circuit = parser.parse(src)
    assert circuit.outputs == {"y"}
    assert not any(i.startswith("1'b") for g in circuit.gates.values() for i in g.inputs)
    assert parser.notes and "tied to a constant" in parser.notes[0]
    for a, b in itertools.product((0, 1), repeat=2):
        assert _eval(circuit, {"a": a, "b": b})["y"] == (1 - a) & b


def test_assign_alias_becomes_buffer():
    src = "module top (a, y);\n input a;\n output y;\n assign y = a;\nendmodule\n"
    circuit = VerilogParser().parse(src)
    assert circuit.gates["y"].gtype == "BUF" and circuit.gates["y"].inputs == ["a"]


def test_iscas_positional_formats_still_parse():
    src = """
module dff (CK,Q,D);
input CK,D;
output Q;
  nmos N7 (M,D,NCK);
endmodule

module s(CK, a, y);
input CK, a;
output y;
  wire q1, q2, n1;
  dff DFF_0(CK,q1,n1);
  delta DFF_1(q2,q1);
  nand NAND2_0(n1, a, q2);
  not NOT_0(y, q1);
endmodule
"""
    circuit = VerilogParser().parse(src)
    assert sorted((ff.q, ff.d) for ff in circuit.flipflops) == [("q1", "n1"), ("q2", "q1")]
    assert circuit.gates["n1"].gtype == "NAND" and circuit.gates["y"].gtype == "NOT"
    assert is_combinational_part_acyclic(circuit)


def test_trojan_benchmark_has_no_false_latch_loop():
    parsed = parse_circuit(open("benchmarks/s38584_scan.v").read(), "s38584_scan.v")
    c = parsed.circuit
    assert is_combinational_part_acyclic(c)
    assert sum(ff.is_scan for ff in c.flipflops) == 1275
    assert sum(ff.q.startswith("TrojanTrigger__") for ff in c.flipflops) == 288
    assert parsed.parse_notes
