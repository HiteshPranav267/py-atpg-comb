"""Full-scan view of a sequential circuit.

Under full scan every flip-flop is a scan cell: the tester shifts any state it
wants into the flip-flops, applies one functional clock, and shifts the
captured state back out. For test generation that makes each flip-flop Q a
pseudo primary input and each flip-flop D a pseudo primary output, leaving a
purely combinational circuit -- which is how industrial ATPG (and open-source
tools such as quaigh's ``expose_dff``) treat scan designs. The sequential
engine instead assumes no scan: state is reachable only by clocking from reset
and observable only at the real outputs, a much harder problem.
"""
from __future__ import annotations

from pyatpg.core.netlist import Circuit

#: Suffix for the buffer that exposes a flip-flop D net as a pseudo output when
#: that net is not itself a gate output (e.g. a shift-register D driven
#: straight from another flip-flop's Q or from a primary input).
PPO_SUFFIX = "__ppo"

#: The parser's tie-off source input (see VerilogParser._tie_net).
TIE_SRC = "__tie_src"


def to_full_scan(circuit: Circuit) -> tuple[Circuit, dict[str, int]]:
    """Return the combinational full-scan model of ``circuit`` and its counts.

    Scan-chain plumbing (scan-in / scan-enable pins that only feed flip-flops)
    disappears with the flip-flops, since shifting is the tester's job, not
    part of the logic under test.
    """
    gates = {out: (g.gtype, list(g.inputs)) for out, g in circuit.gates.items()}
    sources = set(circuit.inputs) | {ff.q for ff in circuit.flipflops}

    # Scan enable is 0 during the capture cycle a full-scan pattern tests.
    # Holding it there matters when logic other than the scan muxes reads
    # it: a Trojan that disables its payload in test mode (Trust-Hub
    # s15850-T100: Trigger_select = Tg2 AND NOT test_se) must not be
    # testable only through a scan-enable value the tester never applies.
    scan_enables = sorted({ff.se for ff in circuit.flipflops
                           if getattr(ff, "is_scan", False) and ff.se in circuit.inputs})
    consts = _fold_constants(gates, {se: 0 for se in scan_enables})

    undriven = []
    outputs = []
    constant_outputs = []
    for po in sorted(circuit.outputs):
        if po in consts:
            constant_outputs.append(po)
        elif po not in gates and po not in sources:
            # An output nothing drives (e.g. an unconnected scan_out port)
            # carries no testable logic; Atalanta rejects it as floating.
            undriven.append(po)
        else:
            outputs.append(po)
    # Flip-flops whose D is tied to a constant (tie-off logic built by the
    # parser) capture the same value every time: exposing D would only add
    # faults on the tie-off net that no pattern can detect.
    tie_nets = {out for out, (_, ins) in gates.items() if set(ins) == {TIE_SRC, TIE_SRC + "_n"}}
    ppos = []
    constant_d = 0
    for ff in circuit.flipflops:
        d = ff.d
        if d in tie_nets or d in consts:
            constant_d += 1
            continue
        if d not in gates:
            # Expose a PI/Q-driven D through a buffer so no net is both an
            # input and an output of the combinational model.
            buf = f"{d}{PPO_SUFFIX}"
            gates.setdefault(buf, ("BUF", [d]))
            d = buf
        if d not in outputs:
            outputs.append(d)
            ppos.append(d)

    # Keep only logic that can reach an output or pseudo output. Dangling
    # gates (an unused QN inverter, logic that only fed a removed pin) have
    # untestable faults by construction, and Atalanta rejects them outright.
    live: set[str] = set()
    stack = list(outputs)
    while stack:
        net = stack.pop()
        if net in live:
            continue
        live.add(net)
        if net in gates:
            stack.extend(gates[net][1])

    comb = Circuit()
    comb.name = getattr(circuit, "name", "circuit")
    comb.logic_clocks = dict(getattr(circuit, "logic_clocks", {}) or {})
    for pi in sorted(circuit.inputs):
        if pi in live and pi not in consts:
            comb.add_input(pi)
    ppis = []
    for ff in circuit.flipflops:
        if ff.q in live and ff.q not in comb.inputs:
            comb.add_input(ff.q)
            ppis.append(ff.q)
    for out, (gtype, ins) in gates.items():
        if out in live:
            comb.add_gate(gtype, out, ins)
    for po in outputs:
        comb.add_output(po)

    return comb, {
        "flip_flops": len(circuit.flipflops),
        "pseudo_inputs": len(ppis),
        "pseudo_outputs": len(ppos),
        "undriven_outputs": undriven,
        "pruned_gates": len(gates) - len(comb.gates),
        "constant_d_flip_flops": constant_d,
        "scan_enables_held_low": scan_enables,
        "constant_outputs": constant_outputs,
    }


def _fold_constants(gates: dict[str, tuple[str, list[str]]], consts: dict[str, int]) -> dict[str, int]:
    """Propagate constant nets through ``gates`` in place; return every constant net.

    Gates whose output becomes constant are removed; the rest are simplified
    (an XNOR left by folding is rebuilt as XOR + NOT for the .bench writer).
    """
    if not consts:
        return {}
    from pyatpg.core.verilog_parser import _fold

    consts = dict(consts)
    fanout: dict[str, list[str]] = {}
    for out, (_, ins) in gates.items():
        for i in ins:
            fanout.setdefault(i, []).append(out)
    work = list(consts)
    while work:
        net = work.pop()
        for out in fanout.get(net, ()):
            if out not in gates:
                continue
            folded = _fold(list(gates[out]), consts)
            if folded is None:
                continue
            if isinstance(folded, int):
                consts[out] = folded
                del gates[out]
                work.append(out)
            else:
                gates[out] = (folded[0], list(folded[1]))
    for out, (gtype, ins) in list(gates.items()):
        if gtype == "XNOR":
            x = f"{out}__xor"
            gates[x] = ("XOR", ins)
            gates[out] = ("NOT", [x])
    return consts
