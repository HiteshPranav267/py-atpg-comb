"""Trigger-centred Trojan detection, independent of signal names.

The older detector in :mod:`pyatpg.atpg.suspects` looks for one shape -- a
rare conjunction feeding a 2-input XOR spliced into a path -- which is how
this tool's own generator builds Trojans. On real Trust-Hub designs it is
noise: on s15850 it flags the same 63 benign gates in the clean and the
infected netlist. What separated those two netlists, measured, is the
trigger itself:

* **Never-toggling clusters.** A trigger is a conjunction of rare
  conditions, so its gates are almost never 1 under random stimulus. Single
  such nets are ordinary (deep comparators); a *connected group* of them is
  not: with 16384 random patterns clean s15850's largest group is 3 gates,
  while T100's two trigger trees are 15 and 6.
* **Logic-clocked registers.** A register clocked by logic instead of the
  clock input is unusual in a single-clock design and is exactly how
  Trust-Hub's sequential triggers are built (T100's ``Tg1``/``Tg2``). The
  logic driving that clock is the trigger; the logic its Q feeds is the
  payload.
* **Rare nets a complete ATPG run never activates.** After a non-targeted
  run, clean s15850 has 1 such net; T100 has 14, 13 of them Trojan gates.
  Isolated misses happen; a connected group of them is a hidden condition.

The first two need only the netlist and are available before ATPG, so they
also feed the targeted fault list and are flagged in the LLM prompt. The
third needs the run's patterns and feeds the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pyatpg.core.netlist import Circuit

from .faultsim import _levelize
from .models import TestVectorSet
from .trojan_metrics import _pattern_values, _random_probabilities

#: Random patterns for the never-toggling test; 16384 keeps ordinary deep
#: logic out (clean s15850's largest never-toggling group drops to 3).
RANDOM_PATTERNS = 16384
RANDOM_SEED = 2024
#: A connected group of never-toggling gates at least this big is a trigger.
MIN_CLUSTER = 5
#: A connected group of never-activated rare nets at least this big.
MIN_UNACTIVATED_CLUSTER = 2
#: Rare: the less likely value appears in under this share of random patterns.
RARE_THRESHOLD = 0.05
#: How far forward from a logic-clocked register's output to mark payload.
PAYLOAD_DEPTH = 3

#: Evidence kinds and how strongly each points at a Trojan on its own.
STRENGTH = {
    "logic_clock": 0.9,
    "unactivated_cluster": 0.85,
    "never_toggling_cluster": 0.75,
    "logic_clock_payload": 0.7,
}

#: Short marks shown to the LLM in the gate table (see llm_context).
PROMPT_MARK = {
    "logic_clock": "clk",
    "never_toggling_cluster": "trig",
    "logic_clock_payload": "pay",
}


@dataclass
class TriggerEvidence:
    """Gate -> list of (kind, reason); strongest kind first per gate."""

    by_gate: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def add(self, gate: str, kind: str, reason: str) -> None:
        entries = self.by_gate.setdefault(gate, [])
        if all(k != kind for k, _ in entries):
            entries.append((kind, reason))
            entries.sort(key=lambda kr: -STRENGTH.get(kr[0], 0))

    def merge(self, other: "TriggerEvidence") -> "TriggerEvidence":
        out = TriggerEvidence()
        for src in (self, other):
            for gate, entries in src.by_gate.items():
                for kind, reason in entries:
                    out.add(gate, kind, reason)
        return out

    def strength(self, gate: str) -> float:
        entries = self.by_gate.get(gate, [])
        if not entries:
            return 0.0
        best = STRENGTH.get(entries[0][0], 0.5)
        # Independent kinds of evidence on one gate corroborate each other.
        return round(min(0.97, best + 0.05 * (len(entries) - 1)), 2)

    def gates(self) -> list[str]:
        return sorted(self.by_gate, key=lambda g: (-self.strength(g), g))

    def prompt_marks(self) -> dict[str, str]:
        marks = {}
        for gate, entries in self.by_gate.items():
            tags = [PROMPT_MARK[k] for k, _ in entries if k in PROMPT_MARK]
            if tags:
                marks[gate] = "+".join(dict.fromkeys(tags))
        return marks

    def to_dict(self) -> dict[str, Any]:
        return {g: [{"kind": k, "reason": r} for k, r in self.by_gate[g]] for g in self.gates()}


def _components(nets: set[str], circuit: Circuit) -> list[list[str]]:
    adj: dict[str, set[str]] = {n: set() for n in nets}
    for out, gate in circuit.gates.items():
        if out in nets:
            for i in gate.inputs:
                if i in nets:
                    adj[out].add(i)
                    adj[i].add(out)
    seen: set[str] = set()
    comps = []
    for start in sorted(nets):
        if start in seen:
            continue
        stack, comp = [start], []
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.append(n)
            stack.extend(adj[n])
        comps.append(sorted(comp))
    return comps


def static_evidence(circuit: Circuit, logic_clocks: dict[str, str] | None = None,
                    *, random_patterns: int = RANDOM_PATTERNS, seed: int = RANDOM_SEED) -> TriggerEvidence:
    """Evidence available from the netlist alone (combinational / full-scan model).

    ``logic_clocks`` maps a register's Q net to the net that clocks it, for
    registers clocked by logic (see VerilogParser._model_logic_clocks).
    """
    ev = TriggerEvidence()
    try:
        order = _levelize(circuit)
    except ValueError:
        return ev
    probs = _random_probabilities(circuit, order, random_patterns, seed)

    never = {n for n, p in probs.items() if p in (0.0, 1.0)}
    for comp in _components(never, circuit):
        if len(comp) >= MIN_CLUSTER:
            for gate in comp:
                ev.add(gate, "never_toggling_cluster",
                       f"one of {len(comp)} connected gates that never changed value in "
                       f"{random_patterns} random patterns -- the shape of a trigger "
                       "built from many rare conditions")

    fanout: dict[str, list[str]] = {}
    for out, gate in circuit.gates.items():
        for i in gate.inputs:
            fanout.setdefault(i, []).append(out)
    for q, clk in sorted((logic_clocks or {}).items()):
        if clk in circuit.gates:
            p1 = probs.get(clk, 0.5)
            rare = min(p1, 1 - p1) < RARE_THRESHOLD
            ev.add(clk, "logic_clock",
                   f"clocks register {q} although it is logic, not the clock input"
                   + (f", and is rarely 1 ({p1:.4f} of random patterns)" if rare else ""))
            # The never-toggling conjunction feeding a rare clock net is the
            # trigger proper. The walk stops at its edge: the nets it taps
            # are the host circuit's own (rare, but not inserted), and
            # following rare nets past it flagged 84 benign gates on T100.
            if rare:
                stack, seen = list(circuit.gates[clk].inputs), set()
                while stack:
                    n = stack.pop()
                    if n in seen or n not in circuit.gates or n not in never:
                        continue
                    seen.add(n)
                    ev.add(n, "logic_clock",
                           f"never-toggling condition feeding the clock of register {q}")
                    stack.extend(circuit.gates[n].inputs)
        # What the logic-clocked register drives is the payload.
        frontier, depth, seen = [q], 0, {q}
        while frontier and depth < PAYLOAD_DEPTH:
            nxt = []
            for n in frontier:
                for out in fanout.get(n, ()):
                    if out in seen or out.startswith(f"{q}__load"):
                        continue
                    seen.add(out)
                    ev.add(out, "logic_clock_payload",
                           f"driven (within {depth + 1} gate(s)) by register {q}, which is "
                           "clocked by logic -- where a sequential Trojan's payload sits")
                    nxt.append(out)
            frontier, depth = nxt, depth + 1
    return ev


def activation_evidence(circuit: Circuit, vectors: TestVectorSet, *,
                        random_patterns: int = 4096, seed: int = RANDOM_SEED) -> TriggerEvidence:
    """Connected groups of rare nets that the run's patterns never activate.

    Meant for a non-targeted (complete) run: a targeted run's patterns are
    written for a handful of faults and leave most rare nets unvisited.
    """
    ev = TriggerEvidence()
    if vectors.count == 0 or not vectors.primary_inputs:
        return ev
    if any(p not in circuit.inputs for p in vectors.primary_inputs):
        return ev
    try:
        order = _levelize(circuit)
    except ValueError:
        return ev
    probs = _random_probabilities(circuit, order, random_patterns, seed)
    values, n = _pattern_values(circuit, order, vectors)
    unactivated = set()
    for net, p1 in probs.items():
        rare_value, p_rare = (1, p1) if p1 <= 0.5 else (0, 1 - p1)
        if p_rare >= RARE_THRESHOLD:
            continue
        ones = bin(values.get(net, 0)).count("1")
        if (ones if rare_value else n - ones) == 0:
            unactivated.add(net)
    for comp in _components(unactivated, circuit):
        if len(comp) >= MIN_UNACTIVATED_CLUSTER:
            for gate in comp:
                ev.add(gate, "unactivated_cluster",
                       f"one of {len(comp)} connected rare nets that none of the {n} "
                       "complete-ATPG patterns drove to its rare value -- a hidden condition")
    return ev


def mark_profiles(profiles: Iterable[dict[str, Any]], evidence: TriggerEvidence) -> None:
    """Attach the prompt marks to the gate profiles the LLM sees."""
    marks = evidence.prompt_marks()
    for p in profiles:
        mark = marks.get(str(p.get("gate_id")))
        if mark:
            p.setdefault("features", {})["mark"] = mark
