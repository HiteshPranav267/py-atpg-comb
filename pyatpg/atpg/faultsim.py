"""Whole-circuit fault simulation of an existing pattern set.

Atalanta reports coverage over the fault list it was *given*. For a targeted
run that list is a few percent of the circuit, so its coverage says nothing
about what the generated patterns achieve on the rest of the circuit -- and
patterns written for the hardest faults routinely catch a large share of the
easy ones on the way to a primary output. This module measures that: it
takes the patterns Atalanta produced and fault-simulates them against every
stuck-at fault on every gate output and primary input, the same list for
every run, so two runs' numbers can be read against each other.

The simulator is bit-parallel: every pattern occupies one bit of a Python
int per net, so a whole pattern set is evaluated in one pass over the
circuit, and each fault costs one event-driven pass over its fan-out cone
that stops at the first primary output showing a difference.

This is a measurement of *patterns*, not a replacement for Atalanta's own
coverage figure, which stays the authority on what the ATPG run detected.
The two use different fault lists (Atalanta's is equivalence-collapsed, this
one is not), so their denominators differ and are always reported.
"""
from __future__ import annotations

import heapq
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pyatpg.core.netlist import Circuit

from .models import TestVectorSet


@dataclass
class PatternCoverage:
    """What one pattern set detects across the whole circuit."""

    #: Faults in the list simulated: two per gate output and per driving PI.
    total_faults: int
    detected_faults: int
    patterns: int
    #: detected / total, as a percentage; None when the list is empty.
    coverage: float | None
    #: Set when simulation could not be trusted (e.g. the fault-free
    #: responses disagree with Atalanta's own), so no number is reported.
    unavailable_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_faults": self.total_faults,
            "detected_faults": self.detected_faults,
            "patterns": self.patterns,
            "coverage": self.coverage,
            "unavailable_reason": self.unavailable_reason,
            "notes": self.notes,
        }


def _unavailable(reason: str, patterns: int = 0) -> PatternCoverage:
    return PatternCoverage(
        total_faults=0,
        detected_faults=0,
        patterns=patterns,
        coverage=None,
        unavailable_reason=reason,
    )


def _levelize(circuit: Circuit) -> list[str]:
    """Gate outputs in an order where every gate's inputs come first."""
    indegree: dict[str, int] = {}
    fanout: dict[str, list[str]] = {}
    for out, gate in circuit.gates.items():
        indegree[out] = 0
        for inp in gate.inputs:
            if inp in circuit.gates:
                indegree[out] += 1
                fanout.setdefault(inp, []).append(out)
    ready = deque(sorted(out for out, deg in indegree.items() if deg == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for nxt in fanout.get(node, ()):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    if len(order) != len(circuit.gates):
        raise ValueError("circuit is not a DAG")
    return order


def _eval(gtype: str, ins: list[int], mask: int) -> int:
    if gtype == "AND":
        v = mask
        for x in ins:
            v &= x
        return v
    if gtype == "OR":
        v = 0
        for x in ins:
            v |= x
        return v
    if gtype == "NAND":
        v = mask
        for x in ins:
            v &= x
        return ~v & mask
    if gtype == "NOR":
        v = 0
        for x in ins:
            v |= x
        return ~v & mask
    if gtype == "NOT":
        return ~ins[0] & mask
    if gtype in ("BUF", "BUFF"):
        return ins[0]
    if gtype == "XOR":
        v = 0
        for x in ins:
            v ^= x
        return v
    if gtype == "XNOR":
        v = 0
        for x in ins:
            v ^= x
        return ~v & mask
    raise ValueError(f"Unsupported gate type: {gtype}")


def _pack(column: list[str]) -> int:
    """Bit i of the result is pattern i's value for one signal.

    An unspecified input ('x') is packed as 0: Atalanta's default fill mode
    (-R) leaves none, so this only matters for a hand-written .vec file, and
    the caller notes it.
    """
    value = 0
    for i, ch in enumerate(column):
        if ch == "1":
            value |= 1 << i
    return value


def simulate_pattern_coverage(
    circuit: Circuit,
    vectors: TestVectorSet,
    time_budget_s: float | None = None,
) -> PatternCoverage:
    """Fault-simulate ``vectors`` over every stuck-at fault in ``circuit``.

    The fault-free response of every pattern is checked against the response
    Atalanta recorded alongside it; any disagreement means this simulator
    and Atalanta are not looking at the same circuit (a name mismatch, an
    unsupported gate), and the result is reported unavailable rather than as
    a number that would be wrong.

    ``time_budget_s`` bounds the wall-clock spent; a circuit that exceeds it
    reports the figure as unavailable instead of a partial count.
    """
    n = vectors.count
    if n == 0:
        return _unavailable("The run produced no test patterns to simulate.")
    if not vectors.primary_inputs:
        return _unavailable("The pattern file names no primary inputs.")

    pis = list(vectors.primary_inputs)
    pos = list(vectors.primary_outputs)
    unknown_pis = [p for p in pis if p not in circuit.inputs]
    unknown_pos = [p for p in pos if p not in circuit.outputs]
    if unknown_pis or unknown_pos:
        names = ", ".join((unknown_pis + unknown_pos)[:5])
        return _unavailable(
            f"Pattern file names signal(s) not in the parsed circuit ({names})."
        )

    try:
        order = _levelize(circuit)
    except ValueError as exc:
        return _unavailable(str(exc))

    mask = (1 << n) - 1
    notes: list[str] = []

    # Fault-free simulation, all patterns at once.
    columns = {name: [] for name in pis}
    saw_x = False
    for vec in vectors.vectors:
        bits = vec.inputs
        if len(bits) != len(pis):
            return _unavailable(
                f"Pattern {vec.index} has {len(bits)} input bits for {len(pis)} inputs."
            )
        for name, ch in zip(pis, bits):
            if ch not in "01":
                saw_x = True
            columns[name].append(ch)
    if saw_x:
        notes.append("Unspecified input bits in the pattern file were simulated as 0.")

    good: dict[str, int] = {name: _pack(col) for name, col in columns.items()}
    # A circuit input the patterns never mention is left at 0 -- it cannot
    # happen for Atalanta's own .vec, which lists every PI, but a partial
    # file must still simulate deterministically.
    for name in circuit.inputs:
        good.setdefault(name, 0)

    gates = circuit.gates
    for out in order:
        gate = gates[out]
        try:
            good[out] = _eval(gate.gtype, [good[i] for i in gate.inputs], mask)
        except (KeyError, ValueError) as exc:
            return _unavailable(f"Could not simulate gate {out}: {exc}")

    # Cross-check against the responses Atalanta wrote next to each pattern.
    if pos and all(vec.outputs for vec in vectors.vectors):
        expected = {name: [] for name in pos}
        for vec in vectors.vectors:
            if len(vec.outputs) != len(pos):
                expected = {}
                break
            for name, ch in zip(pos, vec.outputs):
                expected[name].append(ch)
        for name, col in expected.items():
            if any(ch not in "01" for ch in col):
                continue
            if _pack(col) != good.get(name, 0):
                return _unavailable(
                    "Fault-free simulation disagrees with the ATPG tool's recorded "
                    f"response on output {name}; the pattern set was not scored."
                )

    # Fault sites: every gate output, and every PI that drives something.
    referenced: set[str] = set()
    for gate in gates.values():
        referenced.update(gate.inputs)
    sites = sorted(set(gates) | (set(circuit.inputs) & referenced))

    fanout: dict[str, list[str]] = {}
    for out, gate in gates.items():
        for inp in gate.inputs:
            fanout.setdefault(inp, []).append(out)
    level = {name: i for i, name in enumerate(order)}
    outputs = set(circuit.outputs)

    detected = 0
    deadline = time.monotonic() + time_budget_s if time_budget_s else None
    for index, site in enumerate(sites):
        if deadline is not None and index % 256 == 0 and time.monotonic() > deadline:
            return _unavailable(
                f"Pattern simulation exceeded its {time_budget_s:g}s budget after "
                f"{index} of {len(sites)} fault sites.",
                patterns=n,
            )
        for stuck in (0, mask):
            if good[site] == stuck:
                # No pattern activates the fault: every pattern already
                # drives the net to the stuck value.
                continue
            if _propagates(site, stuck, good, gates, fanout, level, outputs, mask):
                detected += 1

    total = 2 * len(sites)
    return PatternCoverage(
        total_faults=total,
        detected_faults=detected,
        patterns=n,
        coverage=round(detected / total * 100, 3) if total else None,
        notes=notes,
    )


def _propagates(
    site: str,
    stuck: int,
    good: dict[str, int],
    gates: dict[str, Any],
    fanout: dict[str, list[str]],
    level: dict[str, int],
    outputs: set[str],
    mask: int,
) -> bool:
    """Whether any pattern carries the fault effect at ``site`` to a PO.

    Event-driven over the fan-out cone in level order: a gate is only
    re-evaluated once every changed input has settled, and the walk ends at
    the first primary output whose faulty value differs from its good one.
    """
    if site in outputs:
        return True
    faulty: dict[str, int] = {site: stuck}
    # Heap keyed by level so a gate is popped after every gate feeding it.
    pending: list[tuple[int, str]] = []
    queued: set[str] = set()
    for nxt in fanout.get(site, ()):
        if nxt not in queued:
            queued.add(nxt)
            heapq.heappush(pending, (level[nxt], nxt))

    while pending:
        _, out = heapq.heappop(pending)
        gate = gates[out]
        value = _eval(gate.gtype, [faulty.get(i, good[i]) for i in gate.inputs], mask)
        if value == good[out]:
            continue
        faulty[out] = value
        if out in outputs:
            return True
        for nxt in fanout.get(out, ()):
            if nxt not in queued:
                queued.add(nxt)
                heapq.heappush(pending, (level[nxt], nxt))
    return False
