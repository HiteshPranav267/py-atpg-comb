"""Rare-rooted logic cones, summarised for the LLM.

A row of per-gate numbers (type, fan-in/out, SCOAP, toggle rate) is what the
structural heuristic already ranks by, so a model shown only those rows can
at best re-rank the same evidence. What it can judge and a ranking cannot is
*structure*: whether a rare condition is an inserted trigger bolted onto
signals from all over the design, or ordinary rare logic such as a counter's
high bits or an address decode. This module turns the netlist into units of
that judgement.

A cone is a rare net (its rarer value in under ``ROOT_RARE`` of random
patterns) and the fanout-free logic beneath it: walking back from the root,
every gate whose only consumer is already in the cone, up to ``MAX_DEPTH``
levels. Its leaves are the nets that feed it from outside (signals it taps).
Each cone is summarised in one line: size, depth, gate mix, how many nets it
taps and how common and how far apart they are, how rare its root is, and
what the root drives. One line costs about as much as two gate rows, so a
request carries several hundred gates' worth of structure.

A rare net that is the only input of a rare consumer is part of that
consumer's cone, not a cone of its own, so overlapping trees are reported
once, at their top. Names never appear in a summary.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from pyatpg.core.netlist import Circuit

#: A cone's root takes its rarer value in fewer than this share of random patterns.
ROOT_RARE = 0.05
#: Levels walked back from the root.
MAX_DEPTH = 4
#: Gates in a cone, at most (the rest of a huge fanout-free region is left out).
MAX_GATES = 40
#: Cones with fewer gates than this are single rare gates; the gate view covers them.
MIN_GATES = 2
#: Consumers of the root listed in a summary.
MAX_LISTED_CONSUMERS = 3


@dataclass
class Cone:
    root: str
    #: Root first, then breadth-first by depth.
    gates: list[str]
    depth: int
    leaves: list[str]
    consumers: list[str]
    features: dict[str, Any] = field(default_factory=dict)


#: Columns of a cone summary row.
CONE_COLUMNS = (
    "id", "gates", "depth", "types", "leaves", "leaf_p", "leaf_fo", "leaf_lvl",
    "root_rare", "root_fo", "drives", "dup", "level", "flag",
)
CONE_HEADER = ",".join(CONE_COLUMNS)
CONE_LEGEND = (
    "One cone per line, CSV. A cone is a rare net (root) and the logic beneath "
    "it that feeds nothing else. gates/depth: its size. types: gate mix, e.g. "
    "AND4x2 = two 4-input ANDs. leaves: nets feeding the cone from outside; "
    "leaf_p: median probability those nets are 1 under random patterns; "
    "leaf_fo: median number of gates each leaf drives (1 = private to the cone, "
    "higher = a signal shared with other logic and tapped here); leaf_lvl: "
    "logic levels spanned by the leaves, as a share of the circuit's depth (near "
    "0 = all from one place, near 1 = drawn from all over the design). "
    "root_rare: how rarely the root takes its rarer value (0 = never in 4096 "
    "random patterns). root_fo: gates the root drives. drives: those gates' "
    "types, with po when the gate is an output or register input, and sp=x where "
    "x is the median probability of the gate's other inputs. dup = some gate in "
    "the cone reads the same net twice. level: the root's depth in the circuit, "
    "0-1. flag: gates of the cone a deterministic detector already flagged."
)


def find_cones(circuit: Circuit, probs: dict[str, float], *, root_rare: float = ROOT_RARE,
               max_depth: int = MAX_DEPTH, max_gates: int = MAX_GATES,
               min_gates: int = MIN_GATES, flagged: set[str] | None = None) -> list[Cone]:
    """Every rare-rooted cone of at least ``min_gates`` gates, rarest root first."""
    gates = circuit.gates
    outputs = set(circuit.outputs)
    fanout: dict[str, list[str]] = {}
    for out, gate in gates.items():
        for i in gate.inputs:
            users = fanout.setdefault(i, [])
            if out not in users:
                users.append(out)
    rarity = {n: min(p, 1 - p) for n, p in probs.items()}
    level = _levels(circuit)
    top = max(level.values(), default=1) or 1
    flagged = flagged or set()

    def single_user(n: str) -> str | None:
        users = fanout.get(n, [])
        return users[0] if len(users) == 1 and n not in outputs else None

    cones: list[Cone] = []
    for root in gates:
        if rarity.get(root, 1.0) >= root_rare:
            continue
        user = single_user(root)
        if user is not None and rarity.get(user, 1.0) < root_rare:
            continue  # inside a rarer consumer's cone
        members, frontier, depth = [root], [root], 0
        seen = {root}
        leaves: list[str] = []
        while frontier and depth < max_depth and len(members) < max_gates:
            nxt = []
            for n in frontier:
                for i in gates[n].inputs:
                    if i in seen:
                        continue
                    seen.add(i)
                    if i in gates and single_user(i) is not None and len(members) + len(nxt) < max_gates:
                        nxt.append(i)
                    else:
                        leaves.append(i)
            members += nxt
            frontier = nxt
            depth += 1 if nxt else 0
        for n in frontier:  # the walk stopped: what feeds the last level is outside
            leaves.extend(i for i in gates[n].inputs if i not in seen)
        if len(members) < min_gates:
            continue
        leaves = list(dict.fromkeys(leaves))
        cone = Cone(root=root, gates=members, depth=depth, leaves=leaves,
                    consumers=list(fanout.get(root, [])))
        cone.features = _summarise(cone, circuit, probs, rarity, fanout, level, top,
                                   outputs, flagged)
        cones.append(cone)
    cones.sort(key=lambda c: (rarity.get(c.root, 1.0), -len(c.gates), c.root))
    return cones


def _levels(circuit: Circuit) -> dict[str, int]:
    from .faultsim import _levelize

    level: dict[str, int] = {}
    for out in _levelize(circuit):
        level[out] = 1 + max((level.get(i, 0) for i in circuit.gates[out].inputs), default=0)
    return level


def _summarise(cone: Cone, circuit: Circuit, probs: dict[str, float], rarity: dict[str, float],
               fanout: dict[str, list[str]], level: dict[str, int], top: int,
               outputs: set[str], flagged: set[str]) -> dict[str, Any]:
    gates = circuit.gates
    mix = Counter(f"{gates[g].gtype}{len(gates[g].inputs)}" for g in cone.gates)
    leaf_p = [probs[n] for n in cone.leaves if n in probs] or [0.5]
    leaf_fo = [len(fanout.get(n, [])) for n in cone.leaves] or [0]
    leaf_lv = [level.get(n, 0) for n in cone.leaves] or [0]
    drives = []
    for c in cone.consumers[:MAX_LISTED_CONSUMERS]:
        g = gates[c]
        others = [probs.get(i, 0.5) for i in g.inputs if i != cone.root]
        tag = f"{g.gtype}{len(g.inputs)}"
        if c in outputs:
            tag += "+po"
        if others:
            tag += f"+sp={median(others):.2f}"
        drives.append(tag)
    if len(cone.consumers) > MAX_LISTED_CONSUMERS:
        drives.append(f"+{len(cone.consumers) - MAX_LISTED_CONSUMERS}")
    return {
        "gates": len(cone.gates),
        "depth": cone.depth,
        "types": " ".join(f"{t}x{n}" if n > 1 else t for t, n in sorted(mix.items())),
        "leaves": len(cone.leaves),
        "leaf_p": round(median(leaf_p), 2),
        "leaf_fo": median(leaf_fo),
        "leaf_lvl": round((max(leaf_lv) - min(leaf_lv)) / top, 2),
        "root_rare": round(rarity.get(cone.root, 0.0), 4),
        "root_fo": len(cone.consumers),
        "drives": " ".join(drives) or "-",
        "dup": "y" if any(len(set(gates[g].inputs)) < len(gates[g].inputs) for g in cone.gates) else "n",
        "level": round(level.get(cone.root, 0) / top, 2),
        "flag": sum(1 for g in cone.gates if g in flagged),
    }


def cone_line(alias: str, cone: Cone) -> str:
    f = cone.features
    return ",".join(str(x) for x in (
        alias, f["gates"], f["depth"], f["types"], f["leaves"], f["leaf_p"], f["leaf_fo"],
        f["leaf_lvl"], f["root_rare"], f["root_fo"], f["drives"], f["dup"], f["level"], f["flag"],
    ))


def expansion(cone: Cone, max_consumers: int = 4) -> list[str]:
    """The gates a chosen cone puts in the fault list: the cone, then what its root drives."""
    return list(dict.fromkeys(cone.gates + cone.consumers[:max_consumers]))
