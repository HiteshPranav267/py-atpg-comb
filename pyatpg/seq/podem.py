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
    circuit: Circuit, values: dict, topo_order: list[str], fault_node: str, stuck_at: int
) -> tuple[str, int] | None:
    target = 1 - stuck_at
    fault_pair = values[fault_node]
    if fault_pair[0] is X:
        return (fault_node, target)
    if fault_pair[0] != target:
        return None  # activation is contradicted -- this branch is dead

    frontier = _d_frontier(circuit, values, topo_order)
    if not frontier:
        return None
    gate = circuit.gates[frontier[0]]
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
) -> PodemResult:
    """Search for a PI assignment that activates and propagates the fault.

    ``observable_outputs`` is any set of node names treated as "seen by the
    tester" -- for the unrolled multi-cycle netlist this is every primary
    output copy across every frame, since a sequential fault may only show
    up several cycles after activation.
    """
    topo_order = _topo_sort_gates(circuit)
    pi_values: dict[str, int] = {}
    decision_stack: list[tuple[str, int, bool]] = []
    backtracks = 0
    iterations = 0
    max_iterations = max_backtracks * 25 + 100

    while True:
        iterations += 1
        if iterations > max_iterations:
            return PodemResult(success=False, backtracks=backtracks, aborted=True)

        values = _imply(circuit, topo_order, pi_values, fault_node, stuck_at)

        if any(has_fault_effect(values[po]) for po in observable_outputs):
            return PodemResult(success=True, pi_values=dict(pi_values), backtracks=backtracks)

        obj = _objective(circuit, values, topo_order, fault_node, stuck_at)
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
