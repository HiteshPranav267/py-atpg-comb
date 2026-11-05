"""Insert a combinational Trojan with a trigger of chosen rarity into a netlist.

This is the generator behind the project's held-out test sets and the
circuits in ``examples/``. The Trojan is written in the host's own cell
style -- ISCAS primitives (``and g1(out, a, b);``) or SAED-style named-pin
cells (``AND2X1 U1 ( .IN1(a), .IN2(b), .Q(out) );``) -- as:

* a **trigger**: 16 host nets tapped by eight 2-input ANDs, two 4-input ANDs
  and one AND root, ``Tj_Trigger``;
* one or more **payloads**: an XOR of ``Tj_Trigger`` spliced in front of a
  host net, so every reader of that net sees it flipped when the trigger
  fires.

The trigger's rarity is fixed by a rule, never tuned to a result. Taps are
drawn at random until the root's probability of being 1 under random
patterns falls in the band of its class:

* ``never``: no hit in 4,096 patterns;
* ``rare``: 0.1-1%;
* ``uncommon``: 1-4%.

The class is decided by simulating the built netlist, not by the estimate
used while drawing. ATALANTA must also detect ``Tj_Trigger`` stuck-at-0 on
the full-scan model, which proves the trigger can fire and its effect
reaches an output. A candidate that fails either check is drawn again.

Every Trojan net is named ``Tj_*``, so a report can score its fault lists
against them (the ranking never sees names).

    pyatpg-trojan benchmarks/c3540.v --class uncommon --seed 10 -o c3540_trojan.v
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

from pyatpg.atpg.faultsim import _levelize
from pyatpg.atpg.structural import parse_circuit
from pyatpg.atpg.trojan_metrics import _random_probabilities
from pyatpg.core.scan import to_full_scan

#: Root probability band per class, measured on the built netlist.
BANDS = {"never": (0.0, 0.0), "rare": (0.001, 0.01), "uncommon": (0.01, 0.04)}
#: Probability-of-1 range the taps are drawn from, per class: an AND over 16
#: taps near 0.5 is ~1e-5, so a weaker trigger needs mostly-1 taps.
TAP_RANGE = {"never": (0.05, 0.6), "rare": (0.55, 0.9), "uncommon": (0.75, 0.97)}
PATTERNS = 4096
PIN = re.compile(r"\.(\w+)\(\s*([\w\[\]]+)\s*\)")
#: ISCAS'89 primitive style: ``type NAME(out, in, ...);`` and ``dff NAME(CK, Q, D);``.
PRIM = re.compile(r"^(\s*)(and|nand|or|nor|xor|xnor|not|buf|dff)(\s+\w+\s*)\(([^)]*)\)(\s*;)", re.M)


def _style(text: str) -> str:
    """``saed`` for named-pin standard cells (Trust-Hub), else ``primitive`` (ISCAS'89)."""
    return "saed" if re.search(r"\.Q\(", text) else "primitive"


def _connections(text: str) -> tuple[dict[str, int], dict[str, int], set[str]]:
    """Per net: how many output pins drive it, how many input pins read it; port names."""
    drives: dict[str, int] = {}
    reads: dict[str, int] = {}
    if _style(text) == "primitive":
        for _, kind, _, args, _ in PRIM.findall(text):
            nets = [a.strip() for a in args.split(",")]
            out, ins = (nets[1], [nets[0], nets[2]]) if kind == "dff" else (nets[0], nets[1:])
            drives[out] = drives.get(out, 0) + 1
            for n in ins:
                reads[n] = reads.get(n, 0) + 1
    for pin, net in PIN.findall(text):
        if pin in ("Q", "QN"):
            drives[net] = drives.get(net, 0) + 1
        else:
            reads[net] = reads.get(net, 0) + 1
    ports: set[str] = set()
    for decl in re.findall(r"^\s*(?:input|output|inout)\s+([^;]+);", text, flags=re.M):
        ports.update(n.strip() for n in decl.split(",") if n.strip())
    header = re.search(r"module\s+\w+\s*\(([^)]*)\)", text, flags=re.S)
    if header:
        ports.update(n.strip() for n in header.group(1).split(",") if n.strip())
    return drives, reads, ports


def _scan_view(text: str):
    circuit = parse_circuit(text, "host.v").circuit
    if circuit.flipflops:
        circuit, _ = to_full_scan(circuit)
    return circuit


def _probabilities(text: str, circuit=None) -> dict[str, float]:
    circuit = circuit or _scan_view(text)
    return _random_probabilities(circuit, _levelize(circuit), PATTERNS, 1)


def _fanin(circuit, nets: list[str]) -> set[str]:
    """Every net the given nets depend on combinationally."""
    seen: set[str] = set()
    stack = list(nets)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        gate = circuit.gates.get(n)
        if gate is not None:
            stack.extend(gate.inputs)
    return seen


def _trigger_probability(p: dict[str, float], taps: list[str]) -> float:
    """Root probability of the tree over ``taps``, treating the taps as independent.

    An estimate used while drawing; the built netlist is simulated
    afterwards for the recorded value.
    """
    leaves = [p[taps[2 * k]] * p[taps[2 * k + 1]] for k in range(8)]
    half1 = leaves[0] * leaves[1] * leaves[2] * leaves[3]
    half2 = leaves[4] * leaves[5] * leaves[6] * leaves[7]
    return half1 * half2


def trigger_fires(text: str) -> bool:
    """Whether ATPG can drive Tj_Trigger to 1 and see it at an output.

    That is ATALANTA detecting ``Tj_Trigger`` stuck-at-0 on the full-scan
    model: the trigger can fire, and a payload carries it somewhere
    observable. A Trojan that fails this is dead (on s38584 a tap tied to a
    constant made two early variants unable to fire) and tests nothing.
    """
    from pyatpg.atpg.runner import AtalantaExecutionError, AtalantaRunner
    from pyatpg.atpg.workspace import JobWorkspace

    parsed = parse_circuit(text, "variant.v")
    if parsed.circuit.flipflops:
        parsed.circuit, _ = to_full_scan(parsed.circuit)
        parsed.derived = True
    try:
        # Usually 0.1 s; a candidate ATALANTA cannot settle quickly is not
        # proven live and is drawn again.
        run = AtalantaRunner(timeout=60).run(
            JobWorkspace.create("trigger_check"), parsed.to_bench(), label="trigger check",
            targeted_faults=["Tj_Trigger /0"], backtrack_limit=1000)
    except AtalantaExecutionError:
        return False
    return not any(f.fault_name == "Tj_Trigger /0" for f in run.faults)


def _build_primitive(text: str, taps: list[str], hosts: list[str]) -> tuple[str, list[str]]:
    """The same Trojan as :func:`_build`, written as ISCAS'89 primitives."""
    lines = [f"  and Trojan{k + 1}(Tj_OUT{k + 1}, {taps[2 * k]}, {taps[2 * k + 1]});" for k in range(8)]
    lines.append("  and Trojan1234(Tj_OUT1234, Tj_OUT1, Tj_OUT2, Tj_OUT3, Tj_OUT4);")
    lines.append("  and Trojan5678(Tj_OUT5678, Tj_OUT5, Tj_OUT6, Tj_OUT7, Tj_OUT8);")
    lines.append("  and Trojan_Trigger(Tj_Trigger, Tj_OUT1234, Tj_OUT5678);")
    pay = {h: f"{h}_Tj_Payload" for h in hosts}

    def reroute(m: re.Match) -> str:
        # Every read of a payload host now reads the payload; its driver is untouched.
        nets = [a.strip() for a in m.group(4).split(",")]
        reads = {0, 2} if m.group(2) == "dff" else set(range(1, len(nets)))
        nets = [pay.get(n, n) if i in reads else n for i, n in enumerate(nets)]
        return f"{m.group(1)}{m.group(2)}{m.group(3)}({', '.join(nets)}){m.group(5)}"

    body = PRIM.sub(reroute, text)
    for k, h in enumerate(hosts):
        lines.append(f"  xor Trojan_Payload{k + 1}({pay[h]}, {h}, Tj_Trigger);")
    wires = ["Tj_OUT%d" % (k + 1) for k in range(8)] + ["Tj_OUT1234", "Tj_OUT5678", "Tj_Trigger"]
    wires += [pay[h] for h in hosts]
    decl = "  wire " + ", ".join(wires) + ";\n"
    end = body.rindex("endmodule")
    return body[:end] + decl + "\n".join(lines) + "\n" + body[end:], wires


def _build(text: str, taps: list[str], hosts: list[str]) -> tuple[str, list[str]]:
    """The netlist with the Trojan written in, and the Trojan's net names."""
    if _style(text) == "primitive":
        return _build_primitive(text, taps, hosts)
    lines = []
    for k in range(8):
        lines.append(f"  AND2X1 Trojan{k + 1} ( .IN1({taps[2 * k]}), .IN2({taps[2 * k + 1]}), "
                     f".Q(Tj_OUT{k + 1}) );")
    lines.append("  AND4X1 Trojan1234 ( .IN1(Tj_OUT1), .IN2(Tj_OUT2), .IN3(Tj_OUT3), "
                 ".IN4(Tj_OUT4), .Q(Tj_OUT1234) );")
    lines.append("  AND4X1 Trojan5678 ( .IN1(Tj_OUT5), .IN2(Tj_OUT6), .IN3(Tj_OUT7), "
                 ".IN4(Tj_OUT8), .Q(Tj_OUT5678) );")
    lines.append("  AND2X1 Trojan_Trigger ( .IN1(Tj_OUT1234), .IN2(Tj_OUT5678), .Q(Tj_Trigger) );")
    body = text
    for k, h in enumerate(hosts):
        pay = f"{h}_Tj_Payload"
        # Every read of h now reads the payload; the driver still drives h.
        body = re.sub(rf"\.(?!Q\(|QN\()(\w+)\(\s*{re.escape(h)}\s*\)", rf".\1({pay})", body)
        lines.append(f"  XOR2X1 Trojan_Payload{k + 1} ( .IN1({h}), .IN2(Tj_Trigger), .Q({pay}) );")
    wires = ["Tj_OUT%d" % (k + 1) for k in range(8)] + ["Tj_OUT1234", "Tj_OUT5678", "Tj_Trigger"]
    wires += [f"{h}_Tj_Payload" for h in hosts]
    decl = "  wire " + ", ".join(wires) + ";\n"
    end = body.rindex("endmodule")
    return body[:end] + decl + "\n".join(lines) + "\n" + body[end:], wires


def insert_trojan(text: str, cls: str, payloads: int, seed: int,
                  max_builds: int = 200) -> tuple[str, dict]:
    """A Trojan of class ``cls`` whose measured trigger probability is in band and which can fire.

    Returns the new netlist text and a metadata dict (class, seed, taps,
    payload hosts, estimated and measured root probability, rejected
    candidates, the Trojan's nets). The same host, class, payload count and
    seed always give the same netlist.
    """
    if cls not in BANDS:
        raise ValueError(f"unknown class {cls!r}; choose one of {', '.join(BANDS)}")
    circuit = _scan_view(text)
    probs = _probabilities(text, circuit)
    drives, reads, ports = _connections(text)
    rng = random.Random(seed)
    lo, hi = BANDS[cls]
    wired = [n for n in probs if drives.get(n) == 1 and reads.get(n, 0) >= 1 and n not in ports
             and not n.startswith("Tj_")]
    tlo, thi = TAP_RANGE[cls]
    taps_pool = [n for n in wired if tlo <= probs[n] <= thi]
    if len(taps_pool) < 16:
        raise RuntimeError(f"only {len(taps_pool)} nets can serve as {cls} taps; 16 are needed")
    rejected = {"band": 0, "dead": 0}
    for _ in range(max_builds):
        for _ in range(20000):
            taps = rng.sample(taps_pool, 16)
            est = _trigger_probability(probs, taps)
            if cls == "never" and est < 1e-6 or cls != "never" and lo <= est <= hi:
                break
        else:
            raise RuntimeError(f"no tap set lands in band {cls}")
        # A payload in front of a net the trigger itself depends on would close
        # a combinational loop through the trigger.
        upstream = _fanin(circuit, taps)
        hosts = rng.sample([n for n in wired if n not in upstream and n in circuit.gates], payloads)
        out, wires = _build(text, taps, hosts)
        # The estimate treats taps as independent; the class is decided by
        # the built netlist's own simulation.
        root = _probabilities(out).get("Tj_Trigger", 0.0)
        if not (root == 0.0 if cls == "never" else lo <= root <= hi):
            rejected["band"] += 1
            continue
        if not trigger_fires(out):
            rejected["dead"] += 1
            continue
        return out, {
            "class": cls, "seed": seed, "payloads": payloads, "taps": taps,
            "payload_hosts": hosts, "root_probability_estimate": est,
            "root_probability": root, "trigger_fires": True, "rejected": rejected,
            "trojan_nets": wires,
        }
    raise RuntimeError(f"no {cls} Trojan passed in {max_builds} builds ({rejected})")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="pyatpg-trojan",
        description="Insert a combinational Trojan with a trigger of chosen rarity "
                    "(16 tapped nets, AND tree, XOR payloads; gates named Tj_*) into "
                    "an ISCAS-primitive or SAED-cell Verilog netlist. Needs ATALANTA "
                    "for the check that the trigger can fire.",
    )
    ap.add_argument("host", help="Trojan-free gate-level Verilog netlist")
    ap.add_argument("--class", dest="cls", choices=list(BANDS), default="uncommon",
                    help="Trigger rarity: never (no hit in 4,096 random patterns), "
                         "rare (0.1-1%%), uncommon (1-4%%). Default: uncommon")
    ap.add_argument("--payloads", type=int, default=1,
                    help="Host nets to flip when the trigger fires (default 1)")
    ap.add_argument("--seed", type=int, default=1, help="Random seed (default 1)")
    ap.add_argument("-o", "--output",
                    help="Output netlist (default: <host>_gen_<class>_s<seed>.v next to the host). "
                         "Metadata is written beside it as .json")
    args = ap.parse_args(argv)

    host = Path(args.host)
    try:
        text = host.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Could not read {host}: {exc}")
        sys.exit(1)
    out = Path(args.output) if args.output else host.with_name(
        f"{host.stem}_gen_{args.cls}_s{args.seed}.v")
    try:
        body, meta = insert_trojan(text, args.cls, args.payloads, args.seed)
    except Exception as exc:  # noqa: BLE001 - surface the real reason and stop
        print(f"Trojan insertion failed: {exc}")
        sys.exit(1)
    out.write_text(body, encoding="utf-8", newline="\n")
    meta = {"file": out.name, "host": host.stem, **meta}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {out}: {meta['class']} trigger, root probability "
          f"{meta['root_probability']:.4f}, {meta['payloads']} payload(s), "
          f"{sum(meta['rejected'].values())} candidate(s) rejected")


if __name__ == "__main__":
    main()
