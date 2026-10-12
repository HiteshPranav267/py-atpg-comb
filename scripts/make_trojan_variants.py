"""Build the LLM-vs-standard test set from Trust-Hub netlists.

Two kinds of circuit:

* **typo-fixed**: Trust-Hub's s35932-T200 and s38417-T100/T200 read
  ``Tj_OUT1`` twice in ``Trojan1234_NOT`` and never read ``Tj_OUT2``. The
  second read is pointed at ``Tj_OUT2``, as the design intends. Nothing
  else changes.
* **generated**: a combinational Trojan written cleanly (no duplicate input)
  into a Trojan-free scan netlist, in the same cell style: eight 2-input
  leaves over host nets, two 4-input ANDs, one AND root (``Tj_Trigger``),
  and XOR payloads spliced in front of host nets. The trigger's strength is
  fixed by a rule, not tuned to any result: taps are drawn at random until the
  root's random-pattern probability falls in the band of its class --
  ``never`` (no hit in 4096 patterns), ``rare`` (0.1-1%), ``uncommon`` (1-4%),
  measured on the built netlist; and Atalanta must detect ``Tj_Trigger``
  stuck-at-0 (the trigger can fire and its effect reaches an output), or the
  candidate is drawn again.

Every Trojan net is named ``Tj_*`` so the report scores it by name; the LLM
never sees names. A manifest records host, class, seed, root probability and
the Trojan's nets for every file.

    python scripts/make_trojan_variants.py \
        --typo "atpg testing/.../s38417_scan.v=s38417-T100_fixed" ... \
        --host "atpg testing/.../s38417_clean.v=s38417" ... \
        --out experiments/llm-vs-standard/variants
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.atpg.faultsim import _levelize  # noqa: E402
from pyatpg.atpg.structural import parse_circuit  # noqa: E402
from pyatpg.atpg.trojan_metrics import _random_probabilities  # noqa: E402
from pyatpg.core.scan import to_full_scan  # noqa: E402

BANDS = {"never": (0.0, 0.0), "rare": (0.001, 0.01), "uncommon": (0.01, 0.04)}
#: Probability-of-1 range the taps are drawn from, per class: an AND over 16
#: taps near 0.5 is ~1e-5, so a weaker trigger needs mostly-1 taps.
TAP_RANGE = {"never": (0.05, 0.6), "rare": (0.55, 0.9), "uncommon": (0.75, 0.97)}
PATTERNS = 4096
PIN = re.compile(r"\.(\w+)\(\s*([\w\[\]]+)\s*\)")


def fix_typo(text: str) -> str:
    """Point the second Tj_OUT1 read of Trojan1234 at the unused Tj_OUT2."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "Tj_OUT1234" in line and line.count("(Tj_OUT1)") == 2:
            first = line.index("(Tj_OUT1)")
            second = line.index("(Tj_OUT1)", first + 1)
            lines[i] = line[:second] + "(Tj_OUT2)" + line[second + len("(Tj_OUT1)"):]
            return "".join(lines)
    raise ValueError("no Trojan1234 instance reading Tj_OUT1 twice")


def _connections(text: str) -> tuple[dict[str, int], dict[str, int], set[str]]:
    """Per net: how many output pins drive it, how many input pins read it; port names."""
    drives: dict[str, int] = {}
    reads: dict[str, int] = {}
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


def _trigger_probability(p: dict[str, float], taps: list[str], rng_patterns: int = PATTERNS) -> float:
    """Root probability of the tree over ``taps``, by simulation on independent bits.

    Taps are treated as independent, which is an estimate; the generated
    netlist is re-simulated afterwards for the recorded value.
    """
    leaves = [p[taps[2 * k]] * p[taps[2 * k + 1]] for k in range(8)]
    half1 = leaves[0] * leaves[1] * leaves[2] * leaves[3]
    half2 = leaves[4] * leaves[5] * leaves[6] * leaves[7]
    return half1 * half2


def trigger_fires(text: str) -> bool:
    """Whether ATPG can drive Tj_Trigger to 1 and see it at an output.

    That is Atalanta detecting ``Tj_Trigger`` stuck-at-0 on the full-scan
    model: the trigger can fire, and a payload carries it somewhere
    observable. A Trojan that fails this is dead (on s38584 a tap tied to a
    constant made two early variants unable to fire) and tests nothing.
    """
    from pyatpg.atpg.runner import AtalantaRunner
    from pyatpg.atpg.workspace import JobWorkspace

    parsed = parse_circuit(text, "variant.v")
    if parsed.circuit.flipflops:
        parsed.circuit, _ = to_full_scan(parsed.circuit)
        parsed.derived = True
    from pyatpg.atpg.runner import AtalantaExecutionError

    try:
        # Usually 0.1 s; a candidate Atalanta cannot settle quickly is not
        # proven live and is drawn again.
        run = AtalantaRunner(timeout=60).run(
            JobWorkspace.create("trigger_check"), parsed.to_bench(), label="trigger check",
            targeted_faults=["Tj_Trigger /0"], backtrack_limit=1000)
    except AtalantaExecutionError:
        return False
    return not any(f.fault_name == "Tj_Trigger /0" for f in run.faults)


def _build(text: str, taps: list[str], hosts: list[str]) -> tuple[str, list[str]]:
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
    """A Trojan of class ``cls`` whose measured trigger probability is in band and which can fire."""
    circuit = _scan_view(text)
    probs = _probabilities(text, circuit)
    drives, reads, ports = _connections(text)
    rng = random.Random(seed)
    lo, hi = BANDS[cls]
    wired = [n for n in probs if drives.get(n) == 1 and reads.get(n, 0) >= 1 and n not in ports
             and not n.startswith("Tj_")]
    tlo, thi = TAP_RANGE[cls]
    taps_pool = [n for n in wired if tlo <= probs[n] <= thi]
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--typo", action="append", default=[], metavar="FILE=NAME")
    ap.add_argument("--host", action="append", default=[], metavar="FILE=NAME")
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--classes", nargs="+", default=["never", "rare", "uncommon"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for spec in args.typo:
        src, name = spec.split("=", 1)
        text = fix_typo(Path(src).read_text(encoding="utf-8"))
        (out / f"{name}.v").write_text(text, encoding="utf-8", newline="\n")
        manifest.append({"file": f"{name}.v", "kind": "typo-fixed", "source": Path(src).name})
        print("wrote", name)
    for spec in args.host:
        src, host = spec.split("=", 1)
        text = Path(src).read_text(encoding="utf-8")
        for cls in args.classes:
            for seed in args.seeds:
                payloads = 1 + (seed % 2) * 3  # seed 1 -> 4 payloads, seed 2 -> 1
                body, meta = insert_trojan(text, cls, payloads, seed)
                name = f"{host}_gen_{cls}_s{seed}"
                (out / f"{name}.v").write_text(body, encoding="utf-8", newline="\n")
                manifest.append({"file": f"{name}.v", "kind": "generated", "host": host, **meta})
                (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                print("wrote", name, "root p", meta["root_probability"], "rejected", meta["rejected"],
                      flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
