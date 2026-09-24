"""Deterministic, reset-driven, multi-cycle functional and fault simulator.

This is the single source of truth for sequential fault coverage. Nothing
else -- not PODEM, not the LLM layer -- is allowed to claim a fault is
detected; they only produce *candidate* sequences that this module verifies.

Flip-flops reset to 0 at cycle 0 (a plain synchronous reset assumption,
documented rather than configurable-and-silent: pass ``initial_state`` to
override for a circuit with a known different reset polarity).
"""
from __future__ import annotations

from dataclasses import dataclass

from pyatpg.core.netlist import Circuit

_GATE_EVAL = {
    "AND": lambda ins: int(all(ins)),
    "OR": lambda ins: int(any(ins)),
    "NAND": lambda ins: int(not all(ins)),
    "NOR": lambda ins: int(not any(ins)),
    "NOT": lambda ins: int(not ins[0]),
    "BUF": lambda ins: ins[0],
    "BUFF": lambda ins: ins[0],
    "XOR": lambda ins: sum(ins) % 2,
}


def _gate_order(circuit: Circuit) -> list[tuple[str, object, list[str]]]:
    """Gates in topological order, computed once per circuit and cached.

    Re-discovering the order on every evaluation (repeated passes over the
    unresolved gates) dominated sequential ATPG runtime: the fault simulator
    evaluates the circuit once per fault, per candidate sequence, per cycle.
    The cache is keyed on the gate dict's identity and size so a circuit
    edited after a run is re-levelized rather than evaluated stale.
    """
    key = (id(circuit.gates), len(circuit.gates))
    cached = getattr(circuit, "_seq_gate_order", None)
    if cached is not None and cached[0] == key:
        return cached[1]

    gate_outputs = set(circuit.gates)
    indegree: dict[str, int] = {}
    fanout: dict[str, list[str]] = {}
    for name, gate in circuit.gates.items():
        deps = [i for i in gate.inputs if i in gate_outputs]
        indegree[name] = len(deps)
        for d in deps:
            fanout.setdefault(d, []).append(name)
    ready = [n for n, deg in indegree.items() if deg == 0]
    order: list[tuple[str, object, list[str]]] = []
    while ready:
        name = ready.pop()
        gate = circuit.gates[name]
        fn = _GATE_EVAL.get(gate.gtype)
        if fn is None:
            raise ValueError(f"Unsupported gate type: {gate.gtype}")
        order.append((name, fn, gate.inputs))
        for succ in fanout.get(name, ()):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
    if len(order) != len(circuit.gates):
        raise RuntimeError(
            "Combinational loop or unresolved signal inside one time frame; "
            "sequential ATPG requires an acyclic combinational part."
        )
    circuit._seq_gate_order = (key, order)
    return order


def _eval_combinational(
    circuit: Circuit,
    base_values: dict[str, int],
    fault_node: str | None = None,
    fault_stuck_at: int | None = None,
) -> dict[str, int]:
    """Evaluate every gate in topological order, optionally forcing one net stuck.

    ``base_values`` must already contain every primary input and flip-flop Q
    value for this cycle. A stuck-at fault on a PI/FF-Q is forced before
    evaluation starts; a stuck-at fault on a gate output overrides that
    gate's computed value the moment it is produced, so the override
    propagates to every downstream gate exactly like a real fault would.
    """
    values = dict(base_values)
    if fault_node is not None and fault_node in values:
        values[fault_node] = fault_stuck_at

    try:
        for name, fn, inputs in _gate_order(circuit):
            values[name] = fn([values[i] for i in inputs])
            if name == fault_node:
                values[name] = fault_stuck_at
    except KeyError as exc:
        raise RuntimeError(
            f"Combinational loop or unresolved signal inside one time frame ({exc}); "
            "sequential ATPG requires an acyclic combinational part."
        ) from None
    return values


@dataclass
class CycleTrace:
    """Everything observable at one clock cycle."""

    primary_outputs: dict[str, int]
    next_state: dict[str, int]


def simulate(
    circuit: Circuit,
    vectors: list[dict[str, int]],
    initial_state: dict[str, int] | None = None,
    fault_node: str | None = None,
    fault_stuck_at: int | None = None,
) -> list[CycleTrace]:
    """Run ``vectors`` (one PI->value dict per clock cycle) through ``circuit``.

    Returns one :class:`CycleTrace` per cycle, in order. Pass ``fault_node``/
    ``fault_stuck_at`` together to simulate under a single stuck-at fault;
    omit both for the fault-free (good-machine) simulation.
    """
    state = dict(initial_state) if initial_state else {ff.q: 0 for ff in circuit.flipflops}
    trace: list[CycleTrace] = []
    for vector in vectors:
        values = dict(vector)
        values.update(state)
        resolved = _eval_combinational(circuit, values, fault_node, fault_stuck_at)
        pos = {po: resolved[po] for po in circuit.outputs}
        next_state = {ff.q: resolved[ff.d] for ff in circuit.flipflops}
        trace.append(CycleTrace(primary_outputs=pos, next_state=next_state))
        state = next_state
    return trace


def first_divergence(
    circuit: Circuit,
    vectors: list[dict[str, int]],
    good: list[CycleTrace],
    fault_node: str,
    fault_stuck_at: int,
) -> int | None:
    """Simulate under a fault, stopping at the first cycle whose outputs differ.

    Equivalent to ``traces_diverge(good, simulate(..., fault))`` but skips the
    cycles after detection, which is most of them for an easily detected fault.
    """
    state = {ff.q: 0 for ff in circuit.flipflops}
    for cycle, (vector, good_cycle) in enumerate(zip(vectors, good)):
        values = dict(vector)
        values.update(state)
        resolved = _eval_combinational(circuit, values, fault_node, fault_stuck_at)
        for po, v in good_cycle.primary_outputs.items():
            if resolved[po] != v:
                return cycle
        state = {ff.q: resolved[ff.d] for ff in circuit.flipflops}
    return None


def traces_diverge(good: list[CycleTrace], faulty: list[CycleTrace]) -> int | None:
    """First cycle index where a primary output differs, else None."""
    for i, (g, f) in enumerate(zip(good, faulty)):
        if g.primary_outputs != f.primary_outputs:
            return i
    return None
