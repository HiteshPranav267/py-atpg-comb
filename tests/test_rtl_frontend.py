"""RTL front end: detection, archive handling, Yosys synthesis, loop cutting."""

import io
import itertools
import zipfile

import pytest

from pyatpg.atpg.structural import parse_circuit
from pyatpg.core import rtl
from pyatpg.core.netlist import Circuit, FlipFlop
from pyatpg.seq.timeframe import is_combinational_part_acyclic
from tests.test_verilog_parser import _eval

try:
    import yowasp_yosys  # noqa: F401
    HAVE_YOSYS = True
except ImportError:
    import shutil
    HAVE_YOSYS = shutil.which("yosys") is not None

needs_yosys = pytest.mark.skipif(not HAVE_YOSYS, reason="Yosys (yowasp-yosys) not installed")


GATE_LEVEL = """
module dff (CK,Q,D);
input CK,D;
output Q;
reg Q;
always @ (posedge CK)
  Q <= D;
endmodule

module top(CK, a, b, y);
  input CK, a, b;
  output y;
  wire n1, q;
  assign n2 = ~n1;
  nand g1(n1, a, b);
  dff f1(CK, q, n2);
  and g2(y, q, a);
endmodule
"""

ADDER_RTL = """
module add(input [1:0] a, b, output [2:0] s);
  assign s = a + b;
endmodule
"""

COUNTER_RTL = """
module counter(input clk, rst, en, output reg [3:0] q, output wrap);
  always @(posedge clk or posedge rst)
    if (rst) q <= 0; else if (en) q <= q + 1;
  assign wrap = &q;
endmodule
"""

# AES-T900's trigger: state held by an always block with no clock.
UNCLOCKED_COUNTER_RTL = """
module trig(input rst, input [3:0] out, output reg hit);
  reg [3:0] Counter;
  always @(rst, out)
    if (rst == 1) Counter <= 0; else Counter <= Counter + 1;
  always @(rst, Counter)
    if (rst == 1) hit <= 0; else hit <= (Counter == 4'hf);
endmodule
"""


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------

def test_gate_level_netlist_is_not_rtl():
    # The ISCAS dff cell model has an always block; it must not count.
    assert not rtl.needs_synthesis(GATE_LEVEL)


@pytest.mark.parametrize("text", [ADDER_RTL, COUNTER_RTL, UNCLOCKED_COUNTER_RTL])
def test_behavioural_verilog_is_rtl(text):
    assert rtl.needs_synthesis(text)


def test_gate_level_parse_is_unchanged():
    circuit = parse_circuit(GATE_LEVEL, "top.v").circuit
    assert len(circuit.flipflops) == 1
    assert {"n1", "n2", "y"} <= set(circuit.gates)
    assert not any(ff.q.endswith("__loop_d") for ff in circuit.flipflops)


# ----------------------------------------------------------------------
# Archives
# ----------------------------------------------------------------------

def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


TB = "module tb; reg clk; initial begin #10 $finish; end endmodule\n"


def test_archive_prefers_trojan_inserted_copy_and_drops_testbenches():
    data = _zip({
        "AES-T1/src/TjFree/core.v": "module core(input a, output y); assign y = ~a; endmodule\n",
        "AES-T1/src/TjFree/tb.v": TB,
        "AES-T1/src/TjIn/core.v": "module core(input a, b, output y); assign y = a & b; endmodule\n",
        "AES-T1/src/TjIn/trojan.v": "module trojan(input a, output t); assign t = a; endmodule\n",
        "AES-T1/src/TjIn/tb.v": TB,
        "AES-T1/AES-T1.pdf": "%PDF",
    })
    text, name = rtl.decode_upload("AES-T1.zip", data)
    assert name == "AES-T1.v"
    files, notes = rtl.unpack_sources(text)
    assert sorted(files) == ["AES-T1/src/TjIn/core.v", "AES-T1/src/TjIn/trojan.v"]
    assert any("Testbench" in n for n in notes)
    assert any("Trojan-inserted" in n for n in notes)


def test_ambiguous_archive_is_rejected():
    data = _zip({
        "a/core.v": "module core(input a, output y); assign y = a; endmodule\n",
        "b/core.v": "module core(input a, output y); assign y = ~a; endmodule\n",
    })
    with pytest.raises(ValueError, match="same module"):
        rtl.decode_upload("x.zip", data)


def test_archive_without_sources_is_rejected():
    with pytest.raises(ValueError, match="no Verilog"):
        rtl.decode_upload("x.zip", _zip({"readme.txt": "hi"}))


def test_bundle_round_trip():
    files = {"a/x.v": "module x; endmodule", "b.vh": "`define W 4"}
    text = rtl.pack_sources(files, ["one note"])
    assert rtl.is_bundle(text) and rtl.needs_synthesis(text)
    got, notes = rtl.unpack_sources(text)
    assert {k: v.rstrip("\n") for k, v in got.items()} == files
    assert notes == ["one note"]


def test_plain_text_upload_passes_through():
    assert rtl.decode_upload("c17.v", b"module c17; endmodule") == ("module c17; endmodule", "c17.v")


# ----------------------------------------------------------------------
# Circuit clean-up
# ----------------------------------------------------------------------

def test_break_combinational_loops_inserts_pseudo_flip_flops():
    c = Circuit()
    c.add_input("a")
    c.add_output("y")
    c.add_gate("XOR", "Counter", ["a", "_5_"])   # named net in the loop
    c.add_gate("NOT", "_5_", ["Counter"])
    c.add_gate("BUF", "y", ["Counter"])
    assert not is_combinational_part_acyclic(c)
    cuts = rtl.break_combinational_loops(c)
    assert cuts == ["Counter"]  # the RTL name is preferred over the temporary
    assert is_combinational_part_acyclic(c)
    assert [(ff.q, ff.d) for ff in c.flipflops] == [("Counter", "Counter__loop_d")]


def test_remove_dead_logic_drops_unobservable_aliases():
    c = Circuit()
    c.add_input("a")
    c.add_output("y")
    c.add_gate("NOT", "y", ["a"])
    c.add_gate("BUF", "dangling", ["never_driven"])
    c.add_flipflop(FlipFlop("q", "y"))
    assert rtl.remove_dead_logic(c) == 1
    assert c.promote_undefined_signals_to_inputs() == []


# ----------------------------------------------------------------------
# Synthesis (needs Yosys)
# ----------------------------------------------------------------------

@needs_yosys
def test_synthesized_adder_is_functionally_exact():
    circuit = parse_circuit(ADDER_RTL, "add.v").circuit
    assert not circuit.flipflops
    for a, b in itertools.product(range(4), repeat=2):
        vals = {f"a[{i}]": (a >> i) & 1 for i in range(2)}
        vals.update({f"b[{i}]": (b >> i) & 1 for i in range(2)})
        out = _eval(circuit, vals)
        assert sum(out[f"s[{i}]"] << i for i in range(3)) == a + b


@needs_yosys
def test_synthesized_counter_has_its_registers():
    parsed = parse_circuit(COUNTER_RTL, "counter.v")
    assert len(parsed.circuit.flipflops) == 4
    assert {"clk", "rst", "en"} <= parsed.circuit.inputs
    assert not parsed.implicit_inputs
    assert any("Yosys" in n for n in parsed.parse_notes)
    assert is_combinational_part_acyclic(parsed.circuit)


@needs_yosys
def test_unclocked_state_is_cut_into_pseudo_flip_flops():
    parsed = parse_circuit(UNCLOCKED_COUNTER_RTL, "trig.v")
    assert is_combinational_part_acyclic(parsed.circuit)
    assert any("feedback" in n for n in parsed.parse_notes)
    assert any("Counter" in ff.q for ff in parsed.circuit.flipflops)


@needs_yosys
def test_zip_upload_synthesizes_end_to_end():
    data = _zip({
        "d/src/TjIn/top.v": "module top(input clk, input [1:0] a, b, output [2:0] s, output t);\n"
                            "  add u(.a(a), .b(b), .s(s));\n  trojan x(.clk(clk), .a(a[0]), .t(t));\nendmodule\n",
        "d/src/TjIn/add.v": ADDER_RTL,
        "d/src/TjIn/trojan.v": "module trojan(input clk, a, output reg t); always @(posedge clk) t <= a; endmodule\n",
        "d/src/TjIn/tb.v": TB,
    })
    text, name = rtl.decode_upload("d.zip", data)
    parsed = parse_circuit(text, name)
    assert len(parsed.circuit.flipflops) == 1
    assert {"s[0]", "s[1]", "s[2]", "t"} <= parsed.circuit.outputs
    assert any("top module top" in n for n in parsed.parse_notes)


@needs_yosys
def test_synthesis_errors_are_reported():
    with pytest.raises(ValueError, match="Yosys could not synthesize"):
        parse_circuit("module m(input a, output y); missing u(.a(a), .y(y)); assign z = a + 1; endmodule",
                      "m.v")


# ----------------------------------------------------------------------
# Size guard
# ----------------------------------------------------------------------

def test_size_guard_rejects_oversized_designs(monkeypatch):
    monkeypatch.setenv("PYATPG_MAX_GATES", "2")
    with pytest.raises(rtl.DesignTooLargeError, match="PYATPG_MAX_GATES"):
        parse_circuit(GATE_LEVEL, "top.v")


def test_size_guard_only_defaults_on_hosted_backend(monkeypatch):
    monkeypatch.delenv("PYATPG_MAX_GATES", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert rtl.max_gates() == 0
    monkeypatch.setenv("RENDER", "true")
    assert rtl.max_gates() == 60000
    monkeypatch.setenv("PYATPG_MAX_GATES", "0")
    assert rtl.max_gates() == 0
