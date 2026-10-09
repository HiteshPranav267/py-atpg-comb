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
* **Payload splices.** s38584-T100's trigger fires in 10% of random
  full-scan patterns -- not rare at all -- so every rarity signal misses
  it. Its payload does not hide: an OR spliced in front of output g34028,
  fed by a 17-gate tree that does nothing else, lifts that output from 0.4%
  to 10.8% of patterns. Measured over clean s15850 and clean ISCAS s38584,
  no benign output gate has a fanout-free side tree of 6+ gates that moves
  its probability more than 2x.
* **Duplicate gate inputs.** Synthesis never emits a gate reading the same
  net twice; a hand-inserted Trojan can (s38584-T100's NOR(Tj_OUT1,
  Tj_OUT1, ...)). One hit across all four circuits measured, the Trojan's.
* **Payload multiplexers.** s38584-T200/T300 replace output g10122 with a
  2:1 mux whose select is the trigger. The parser builds it as NOT(s),
  AND(d0, NOT s), AND(d1, s), OR; the payload-depth walk from the trigger
  registers reached only part of it. A mux whose select is already a strong
  suspect is flagged whole (the original data input is the host's own
  signal and stays out).
* **Autonomous timers.** s38584-T300 counts activity over a 50-cycle
  window kept by a 32-bit counter on the ordinary clock: its next state
  depends only on itself, so it is clocked normally, toggles freely and
  feeds no output -- every other sign misses it. A group of registers whose
  next state reads nothing but the group is a free-running timer that takes
  no input from the design. Across the ~50 clean ISCAS'89 netlists in
  benchmarks/ (and clean s38584 / s15850) the largest such group is a single
  register; T300 has exactly its 32-bit window counter.

Calibration on clean ISCAS s38584 (a 36-gate never-toggling group, and
several 7-gate ones) showed never-toggling groups also occur in benign
logic, so on their own they are a lead, not a finding.

All but the never-activated nets need only the netlist and are available
before ATPG, so they also feed the targeted fault list and are flagged in
the LLM prompt. The never-activated nets need the run's patterns and feed
the verdict.
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
#: How many levels back from the flagged logic the region grows.
REGION_DEPTH = 2
#: A payload splice: the side tree has at least this many gates ...
MIN_SPLICE_TREE = 6
#: ... and changes the output's probability by at least this factor.
MIN_SPLICE_RATIO = 5.0
#: Registers in a self-contained group before it counts as a timer (a lone
#: self-looped register is ordinary; clean s38584 has one).
MIN_TIMER = 4

#: Evidence kinds and how strongly each points at a Trojan on its own. Below
#: the verdict's 0.7 a kind is a lead: it needs a second kind of evidence on
#: the same gate (+0.05 each) to make the verdict "Trojan suspected".
STRENGTH = {
    "logic_clock": 0.9,
    "payload_splice": 0.85,
    "payload_mux": 0.8,
    "trojan_timer": 0.8,
    "logic_clock_payload": 0.7,
    "trojan_region": 0.7,
    "unactivated_cluster": 0.65,
    "duplicate_input": 0.65,
    "never_toggling_cluster": 0.6,
    "autonomous_timer": 0.6,
}

#: Which mechanism each kind of evidence measures; kinds in the same family
#: are not independent and do not add up (see TriggerEvidence.strength).
FAMILY = {
    "never_toggling_cluster": "rarity",
    "unactivated_cluster": "rarity",
    "logic_clock": "clock",
    "logic_clock_payload": "clock",
    "payload_splice": "payload",
    "payload_mux": "payload",
    "duplicate_input": "hand_edit",
    "trojan_region": "region",
    "autonomous_timer": "timer",
    "trojan_timer": "timer",
}

#: Short marks shown to the LLM in the gate table (see llm_context).
PROMPT_MARK = {
    "logic_clock": "clk",
    "never_toggling_cluster": "trig",
    "logic_clock_payload": "pay",
    "payload_splice": "pay",
    "payload_mux": "pay",
    "duplicate_input": "dup",
    "autonomous_timer": "tmr",
    "trojan_timer": "tmr",
}


@dataclass
class TriggerEvidence:
    """Gate -> list of (kind, reason); strongest kind first per gate."""

    by_gate: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    #: Host-circuit nets at the Trojan's edge (signals its trigger taps, the
    #: original signal a payload replaced): never grown into.
    host: set[str] = field(default_factory=set)

    def add(self, gate: str, kind: str, reason: str) -> None:
        entries = self.by_gate.setdefault(gate, [])
        if all(k != kind for k, _ in entries):
            entries.append((kind, reason))
            entries.sort(key=lambda kr: -STRENGTH.get(kr[0], 0))

    def merge(self, other: "TriggerEvidence") -> "TriggerEvidence":
        out = TriggerEvidence(host=self.host | other.host)
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
        # Only independent mechanisms corroborate each other: a never-toggling
        # net is also never activated by ATPG almost by definition, and adding
        # those up raised clean ISCAS s38584 to "Trojan suspected".
        families = {FAMILY.get(kind, kind) for kind, _ in entries}
        return round(min(0.97, best + 0.05 * (len(families) - 1)), 2)

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
            consumers = fanout.setdefault(i, [])
            if out not in consumers:
                consumers.append(out)

    for out, gate in circuit.gates.items():
        if len(set(gate.inputs)) < len(gate.inputs):
            ev.add(out, "duplicate_input",
                   "reads the same net on two inputs -- synthesis never emits this; "
                   "hand-inserted logic can")

    for out, side, tree, ratio, original in _payload_splices(circuit, fanout, probs):
        ev.host.add(original)
        ev.add(out, "payload_splice",
               f"output gate whose side input comes from a {len(tree)}-gate tree that "
               f"feeds nothing else, changing the output's probability {ratio:.0f}x -- "
               "the shape of a payload spliced in front of an output")
        for gate in tree:
            ev.add(gate, "payload_splice",
                   f"part of the {len(tree)}-gate side tree spliced into output {out}")

    _logic_clock_evidence(ev, circuit, logic_clocks, probs, never, fanout)
    ev = complete_region(circuit, ev, logic_clocks)
    # After the region: a timer counts as a Trojan's when it feeds the
    # registers' load logic the region adds. Its logic is closed (nothing
    # outside feeds it), so there is nothing further to grow into.
    _autonomous_timers(ev, circuit, order, fanout)
    _payload_muxes(ev, circuit)
    return ev


def _payload_muxes(ev: TriggerEvidence, circuit: Circuit) -> None:
    """2:1 muxes (OR/NOR of AND(d0, NOT s), AND(d1, s)) selected by a strong suspect."""
    strong = {g for g in ev.by_gate if ev.strength(g) >= 0.7}
    if not strong:
        return
    # The parser models a logic-clocked register as the same mux with its
    # clock as select (Q' = clk ? D : Q): state, not a payload.
    clocks = set((getattr(circuit, "logic_clocks", None) or {}).values())
    gates = circuit.gates
    for out, gate in gates.items():
        if gate.gtype not in ("OR", "NOR") or len(gate.inputs) != 2:
            continue
        legs = [gates.get(i) for i in gate.inputs]
        if any(g is None or g.gtype != "AND" or len(g.inputs) != 2 for g in legs):
            continue
        for (t_n, t_nsel), (t_s, t_sel) in (
            ((gate.inputs[0], legs[0]), (gate.inputs[1], legs[1])),
            ((gate.inputs[1], legs[1]), (gate.inputs[0], legs[0])),
        ):
            nots = [i for i in t_nsel.inputs if i in gates and gates[i].gtype == "NOT"]
            sel = next((s for s in t_sel.inputs
                        for n in nots if gates[n].inputs == [s]), None)
            if sel is None or sel not in strong or sel in ev.host or sel in clocks:
                continue
            nsel = next(n for n in nots if gates[n].inputs == [sel])
            why = f"part of a 2:1 multiplexer selected by flagged Trojan logic {sel}"
            for n in (nsel, t_n, t_s, out):
                ev.add(n, "payload_mux", why)
            break


def _autonomous_timers(ev: TriggerEvidence, circuit: Circuit, order: list[str],
                       fanout: dict[str, list[str]]) -> None:
    """Register groups whose next state depends only on the group itself.

    Needs the full-scan model's ``scan_cells`` (Q -> captured D net). A
    timer alone is a lead -- real designs have benign free-running counters
    -- and becomes a Trojan timer when its logic feeds gates already flagged
    by an independent mechanism (T300's window counter feeds its
    trigger).
    """
    cells: dict[str, str] = getattr(circuit, "scan_cells", None) or {}
    if not cells:
        return
    group = set(cells)
    while True:
        # Gates computed from the candidate registers alone.
        pure: set[str] = set()
        for out in order:
            ins = circuit.gates[out].inputs
            if ins and all(i in pure or i in group for i in ins):
                pure.add(out)
        keep = {q for q in group if cells[q] in pure}
        if keep == group:
            break
        group = keep
    if not group:
        return

    # Split into separate timers: a register joins the registers its D reads.
    parent = {n: n for n in group | pure}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for out in pure:
        for i in circuit.gates[out].inputs:
            parent[find(i)] = find(out)
    for q in group:
        parent[find(q)] = find(cells[q])
    members: dict[str, list[str]] = {}
    for n in group | pure:
        members.setdefault(find(n), []).append(n)

    strong = {g for g in ev.by_gate if ev.strength(g) >= 0.7}
    for nets in members.values():
        regs = [n for n in nets if n in group]
        if len(regs) < MIN_TIMER:
            continue
        logic = [n for n in nets if n in circuit.gates]
        feeds = sorted({c for n in nets for c in fanout.get(n, ()) if c in strong} - set(nets))
        if feeds:
            kind = "trojan_timer"
            why = (f"part of a {len(regs)}-register free-running timer (its next state reads "
                   f"nothing but itself) whose logic feeds flagged Trojan logic ({feeds[0]}"
                   + (" ..." if len(feeds) > 1 else "") + ")")
        else:
            kind = "autonomous_timer"
            why = (f"part of a {len(regs)}-register free-running timer: its next state reads "
                   "nothing but itself, so it counts time with no input from the design")
        for n in regs + logic:
            ev.add(n, kind, why)


def _logic_clock_evidence(ev: TriggerEvidence, circuit: Circuit,
                          logic_clocks: dict[str, str] | None, probs: dict[str, float],
                          never: set[str], fanout: dict[str, list[str]]) -> None:
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
                stack, seen = list(circuit.gates[clk].inputs), {clk}
                while stack:
                    n = stack.pop()
                    if n in seen or n not in circuit.gates or n not in never:
                        continue
                    if not set(fanout.get(n, [])) <= seen:
                        # Also read by ordinary logic: a host signal the
                        # trigger taps, not part of it.
                        ev.host.add(n)
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


def complete_region(circuit: Circuit, ev: TriggerEvidence,
                    logic_clocks: dict[str, str] | None = None) -> TriggerEvidence:
    """Add the rest of the Trojan around its strongly flagged gates.

    The aim is every Trojan gate, not the most suspicious few. On s15850-T100
    the flagged trigger trees left out their own leaf conditions (rare, but
    not never-toggling) and the trigger registers. Two rules close that gap
    without reaching into host logic:

    * a gate whose every consumer is already flagged exists only to feed the
      Trojan, so it is part of it (repeated up to REGION_DEPTH levels back);
    * a logic-clocked register whose clock logic is flagged is the Trojan's
      state, together with the load logic modelling it.

    Gates that also feed ordinary logic stay out, and a circuit with no
    strong suspect (every clean circuit measured) grows nothing.
    """
    region = {g for g in ev.by_gate if ev.strength(g) >= 0.7}
    if not region:
        return ev
    consumers: dict[str, set[str]] = {}
    for out, gate in circuit.gates.items():
        for i in gate.inputs:
            consumers.setdefault(i, set()).add(out)

    for q, clk in sorted((logic_clocks or {}).items()):
        if clk in region:
            ev.add(q, "trojan_region", f"register clocked by the flagged logic {clk}")
            region.add(q)
            # The load mux the parser built for it (Q' = clk ? D : Q).
            for net in circuit.gates:
                if net.startswith(f"{q}__") and net not in region:
                    ev.add(net, "trojan_region", f"load logic of Trojan register {q}")
                    region.add(net)

    frontier = set(region)
    for _ in range(REGION_DEPTH):
        added = set()
        for flagged in frontier:
            gate = circuit.gates.get(flagged)
            if gate is None:
                continue
            for src in gate.inputs:
                if (src in region or src not in circuit.gates or src in ev.host
                        or src in circuit.outputs):
                    continue
                users = consumers.get(src, set())
                if users and users <= region:
                    ev.add(src, "trojan_region",
                           f"feeds only flagged Trojan logic ({sorted(users)[0]}"
                           + (" ..." if len(users) > 1 else "") + ")")
                    added.add(src)
        region |= added
        frontier = added
        if not added:
            break
    return ev


def _side_tree(circuit: Circuit, fanout: dict[str, list[str]], root: str) -> list[str] | None:
    """Gates of the fanout-free tree ending at ``root``; None if any branch leaves it."""
    nodes, stack, seen = [], [root], set()
    while stack:
        n = stack.pop()
        gate = circuit.gates.get(n)
        if gate is None or n in seen:
            continue
        if n != root and len(fanout.get(n, [])) != 1:
            return None
        seen.add(n)
        nodes.append(n)
        stack.extend(gate.inputs)
    return nodes


def _payload_splices(circuit: Circuit, fanout: dict[str, list[str]],
                     probs: dict[str, float]) -> list[tuple[str, str, list[str], float, str]]:
    """(output gate, side input, side tree, probability ratio, original input) per splice."""
    found = []
    for out in sorted(circuit.outputs):
        gate = circuit.gates.get(out)
        if gate is None or gate.gtype not in ("AND", "OR", "NAND", "NOR", "XOR") \
                or len(set(gate.inputs)) != 2:
            continue
        a, b = sorted(set(gate.inputs))
        for side, other in ((a, b), (b, a)):
            if len(fanout.get(side, [])) != 1 or other not in probs or out not in probs:
                continue
            tree = _side_tree(circuit, fanout, side)
            if not tree or len(tree) < MIN_SPLICE_TREE:
                continue
            p_out, p_other = probs[out], probs[other]
            # Compare the rarer value of each: an OR payload lifts a mostly-0
            # output, an AND payload pulls a mostly-1 output down.
            r_out, r_other = min(p_out, 1 - p_out), min(p_other, 1 - p_other)
            ratio = (max(r_out, r_other) + 1e-4) / (min(r_out, r_other) + 1e-4)
            if ratio >= MIN_SPLICE_RATIO:
                found.append((out, side, tree, ratio, other))
    return found


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
