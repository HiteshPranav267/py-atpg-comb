"""PODEM search over a time-frame-unrolled circuit.

This is ordinary combinational PODEM (Goel, 1981): objective -> backtrace ->
imply -> check, with backtracking bounded by a decision-flip counter. It
operates on the *unrolled* netlist built by :mod:`timeframe`, which is why a
single combinational-style search algorithm is enough to cover multiple
clock cycles -- the "sequential-ness" is entirely encoded in how the
netlist was built, not in the search.

A successful search returns a candidate PI assignment across every frame.
It is a candidate, not a verified test: :mod:`engine` re-checks it against
the real, reset-driven simulator before it is ever counted as a detection.
"""
from __future__ import annotations

import heapq
import weakref
from dataclasses import dataclass, field

from pyatpg.core.netlist import Circuit

from .dalgebra import X, eval_pair, has_fault_effect, inverts, is_controlling_value


@dataclass
class PodemResult:
    success: bool
    pi_values: dict[str, int] = field(default_factory=dict)
    backtracks: int = 0
    aborted: bool = False


def _topo_sort_gates(circuit: Circuit) -> list[str]:
    resolved = set(circuit.inputs)
    order: list[str] = []
    unresolved = dict(circuit.gates)
    while unresolved:
        progress = False
        for name, gate in list(unresolved.items()):
            if all(i in resolved for i in gate.inputs):
                order.append(name)
                resolved.add(name)
                del unresolved[name]
                progress = True
        if not progress:
            raise RuntimeError("Unrolled circuit is not acyclic; cannot run PODEM.")
    return order


def _imply(
    circuit: Circuit,
    topo_order: list[str],
    pi_values: dict[str, int],
    fault_node: str,
    stuck_at: int,
) -> dict[str, tuple]:
    values: dict[str, tuple] = {}
    for pi in circuit.inputs:
        v = pi_values.get(pi)
        pair = (v, v) if v is not None else (X, X)
        if pi == fault_node:
            pair = (pair[0], stuck_at)
        values[pi] = pair

    for node in topo_order:
        gate = circuit.gates[node]
        input_pairs = [values[i] for i in gate.inputs]
        pair = eval_pair(gate.gtype, input_pairs)
        if node == fault_node:
            pair = (pair[0], stuck_at)
        values[node] = pair
    return values


# Opcodes for the compiled evaluator.
_AND, _NAND, _OR, _NOR, _NOT, _BUF, _XOR = range(7)
_OPCODES = {"AND": _AND, "NAND": _NAND, "OR": _OR, "NOR": _NOR, "NOT": _NOT,
            "BUF": _BUF, "BUFF": _BUF, "XOR": _XOR}


def _eval_fast(op: int, pairs: list) -> tuple:
    """:func:`dalgebra.eval_pair` for a compiled opcode, without the generic overhead.

    Same 3-valued rules per half: AND is 0 if any input is 0, else X if any
    is X, else 1; OR dually; XOR is X if any input is X; NAND/NOR/NOT invert.
    """
    if op <= _NAND:
        g = f = 1
        for a, b in pairs:
            if a == 0:
                g = 0
            elif a is None and g == 1:
                g = None
            if b == 0:
                f = 0
            elif b is None and f == 1:
                f = None
        if op == _NAND:
            g = None if g is None else 1 - g
            f = None if f is None else 1 - f
        return (g, f)
    if op <= _NOR:
        g = f = 0
        for a, b in pairs:
            if a == 1:
                g = 1
            elif a is None and g == 0:
                g = None
            if b == 1:
                f = 1
            elif b is None and f == 0:
                f = None
        if op == _NOR:
            g = None if g is None else 1 - g
            f = None if f is None else 1 - f
        return (g, f)
    if op == _NOT:
        a, b = pairs[0]
        return (None if a is None else 1 - a, None if b is None else 1 - b)
    if op == _BUF:
        return pairs[0]
    g = f = 0
    for a, b in pairs:
        g = None if (g is None or a is None) else g ^ a
        f = None if (f is None or b is None) else f ^ b
    return (g, f)


class _Compiled:
    """Per-unrolled-circuit structure the incremental implicator needs."""

    def __init__(self, circuit: Circuit, topo_order: list[str]):
        self.inputs = list(circuit.inputs)
        self.order = topo_order
        self.index = {n: i for i, n in enumerate(topo_order)}
        self.ops = {}
        self.ins = {}
        fanout: dict[str, list[str]] = {}
        for node in topo_order:
            gate = circuit.gates[node]
            op = _OPCODES.get(gate.gtype.upper())
            if op is None:
                raise ValueError(f"Unsupported gate type: {gate.gtype}")
            self.ops[node] = op
            self.ins[node] = tuple(gate.inputs)
            for i in gate.inputs:
                fanout.setdefault(i, []).append(node)
        self.fanout = fanout
        self._baseline: dict[str, tuple] | None = None

    def baseline(self) -> dict[str, tuple]:
        """Fault-free values with every primary input X, computed once."""
        if self._baseline is None:
            values: dict[str, tuple] = {pi: (X, X) for pi in self.inputs}
            ops, ins = self.ops, self.ins
            for node in self.order:
                values[node] = _eval_fast(ops[node], [values[i] for i in ins[node]])
            self._baseline = values
        return self._baseline

    def cone(self, node: str) -> list[str]:
        """Gates in ``node``'s transitive fan-out, in topological order."""
        seen, stack = set(), [node]
        while stack:
            for out in self.fanout.get(stack.pop(), ()):
                if out not in seen:
                    seen.add(out)
                    stack.append(out)
        index = self.index
        return sorted(seen, key=index.__getitem__)


_compiled_cache: "weakref.WeakKeyDictionary[Circuit, tuple[int, _Compiled]]" = weakref.WeakKeyDictionary()


def _compiled(circuit: Circuit, topo_order: list[str]) -> _Compiled:
    hit = _compiled_cache.get(circuit)
    if hit is not None and hit[0] == id(topo_order) and hit[1].order is topo_order:
        return hit[1]
    comp = _Compiled(circuit, topo_order)
    _compiled_cache[circuit] = (id(topo_order), comp)
    return comp


class _Implicator:
    """Event-driven :func:`_imply`: re-evaluates only the cone of changed inputs.

    PODEM changes one primary input per decision (a few on backtrack), and
    the values are a pure function of the input assignment, so propagating
    just those changes through their fan-out -- in topological order --
    yields exactly what a full re-simulation would, at a fraction of the cost
    (the full pass was ~85% of a no-scan s5378 run).
    """

    def __init__(self, comp: _Compiled, fault_node: str, stuck_at: int):
        self.comp = comp
        self.fault_node = fault_node
        self.stuck_at = stuck_at
        self.pis: dict[str, int | None] = {pi: None for pi in comp.inputs}
        #: Only gates downstream of the fault can ever carry D/D', so the
        #: D-frontier is searched here instead of the whole circuit.
        self.cone = comp.cone(fault_node)
        # Start from the shared all-X fault-free state and propagate just the
        # fault injection, rather than re-simulating every gate per fault.
        values = dict(comp.baseline())
        self.values = values
        if fault_node in values:
            pair = (values[fault_node][0], stuck_at)
            if pair != values[fault_node]:
                values[fault_node] = pair
                heap: list[int] = []
                queued: set[str] = set()
                self._enqueue_fanout(fault_node, heap, queued)
                self._propagate(heap, queued)

    def _enqueue_fanout(self, node: str, heap: list[int], queued: set[str]) -> None:
        index = self.comp.index
        for out in self.comp.fanout.get(node, ()):
            if out not in queued:
                queued.add(out)
                heapq.heappush(heap, index[out])

    def _propagate(self, heap: list[int], queued: set[str]) -> None:
        comp, values, fault_node, stuck_at = self.comp, self.values, self.fault_node, self.stuck_at
        order, ops, ins = comp.order, comp.ops, comp.ins
        while heap:
            node = order[heapq.heappop(heap)]
            pair = _eval_fast(ops[node], [values[i] for i in ins[node]])
            if node == fault_node:
                pair = (pair[0], stuck_at)
            if pair == values[node]:
                continue
            values[node] = pair
            self._enqueue_fanout(node, heap, queued)

    def update(self, pi_values: dict[str, int]) -> dict[str, tuple]:
        values, fault_node, stuck_at = self.values, self.fault_node, self.stuck_at
        heap: list[int] = []
        queued: set[str] = set()
        for pi, old in self.pis.items():
            new = pi_values.get(pi)
            if new == old:
                continue
            self.pis[pi] = new
            pair = (new, new) if new is not None else (X, X)
            if pi == fault_node:
                pair = (pair[0], stuck_at)
            if pair == values[pi]:
                continue
            values[pi] = pair
            self._enqueue_fanout(pi, heap, queued)
        self._propagate(heap, queued)
        return values


def _d_frontier(circuit: Circuit, values: dict, topo_order: list[str]) -> list[str]:
    frontier = []
    for node in topo_order:
        pair = values[node]
        if pair[0] is X and pair[1] is X:
            gate = circuit.gates[node]
            if any(has_fault_effect(values[i]) for i in gate.inputs):
                frontier.append(node)
    return frontier


def _objective(
    circuit: Circuit, values: dict, topo_order: list[str], fault_node: str, stuck_at: int,
    cone: list[str] | None = None,
) -> tuple[str, int] | None:
    target = 1 - stuck_at
    fault_pair = values[fault_node]
    if fault_pair[0] is X:
        return (fault_node, target)
    if fault_pair[0] != target:
        return None  # activation is contradicted -- this branch is dead

    # Only the first D-frontier gate is ever used, so stop at it rather than
    # scanning the whole unrolled circuit (_d_frontier's order, same pick).
    first = None
    for node in (cone if cone is not None else topo_order):
        pair = values[node]
        if pair[0] is X and pair[1] is X:
            if any(has_fault_effect(values[i]) for i in circuit.gates[node].inputs):
                first = node
                break
    if first is None:
        return None
    gate = circuit.gates[first]
    cval = is_controlling_value(gate.gtype)
    noncontrolling = (1 - cval) if cval is not None else 0
    for i in gate.inputs:
        if values[i][0] is X and values[i][1] is X:
            return (i, noncontrolling)
    return None


def _backtrace(circuit: Circuit, values: dict, node: str, value: int) -> tuple[str, int]:
    while node in circuit.gates:
        gate = circuit.gates[node]
        if inverts(gate.gtype):
            value = 1 - value
        candidates = [i for i in gate.inputs if values[i][0] is X and values[i][1] is X]
        node = candidates[0] if candidates else gate.inputs[0]
    return node, value


def podem(
    circuit: Circuit,
    fault_node: str,
    stuck_at: int,
    observable_outputs: set[str],
    max_backtracks: int = 300,
    topo_order: list[str] | None = None,
) -> PodemResult:
    """Search for a PI assignment that activates and propagates the fault.

    ``observable_outputs`` is any set of node names treated as "seen by the
    tester" -- for the unrolled multi-cycle netlist this is every primary
    output copy across every frame, since a sequential fault may only show
    up several cycles after activation.

    ``topo_order`` may be passed in (from :func:`_topo_sort_gates`) when the
    same circuit is searched for many faults, to avoid re-levelizing it on
    every call.
    """
    if topo_order is None:
        topo_order = _topo_sort_gates(circuit)
    implicator = _Implicator(_compiled(circuit, topo_order), fault_node, stuck_at)
    pi_values: dict[str, int] = {}
    decision_stack: list[tuple[str, int, bool]] = []
    backtracks = 0
    iterations = 0
    max_iterations = max_backtracks * 25 + 100

    while True:
        iterations += 1
        if iterations > max_iterations:
            return PodemResult(success=False, backtracks=backtracks, aborted=True)

        values = implicator.update(pi_values)

        if any(has_fault_effect(values[po]) for po in observable_outputs):
            return PodemResult(success=True, pi_values=dict(pi_values), backtracks=backtracks)

        obj = _objective(circuit, values, topo_order, fault_node, stuck_at, implicator.cone)
        target_pi: tuple[str, int] | None = None
        if obj is not None:
            node, val = obj
            pi, pi_val = _backtrace(circuit, values, node, val)
            if pi not in pi_values:
                target_pi = (pi, pi_val)

        if target_pi is None:
            backtracks += 1
            if backtracks > max_backtracks:
                return PodemResult(success=False, backtracks=backtracks, aborted=True)
            flipped = False
            while decision_stack:
                pi, tried, both = decision_stack.pop()
                if not both:
                    pi_values[pi] = 1 - tried
                    decision_stack.append((pi, 1 - tried, True))
                    flipped = True
                    break
                del pi_values[pi]
            if not flipped:
                return PodemResult(success=False, backtracks=backtracks, aborted=False)
            continue

        pi, pi_val = target_pi
        pi_values[pi] = pi_val
        decision_stack.append((pi, pi_val, False))
