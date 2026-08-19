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


def _eval_combinational(
    circuit: Circuit,
    base_values: dict[str, int],
    fault_node: str | None = None,
    fault_stuck_at: int | None = None,
) -> dict[str, int]:
    """Evaluate every gate to a fixpoint, optionally forcing one net stuck.

    ``base_values`` must already contain every primary input and flip-flop Q
    value for this cycle. A stuck-at fault on a PI/FF-Q is forced before
    evaluation starts; a stuck-at fault on a gate output overrides that
    gate's computed value the moment it is produced, so the override
    propagates to every downstream gate exactly like a real fault would.
    """
    values = dict(base_values)
    if fault_node is not None and fault_node in values:
        values[fault_node] = fault_stuck_at

    unresolved = dict(circuit.gates)
    while unresolved:
        progress = False
        for name, gate in list(unresolved.items()):
            try:
                ins = [values[i] for i in gate.inputs]
            except KeyError:
                continue
            fn = _GATE_EVAL.get(gate.gtype)
            if fn is None:
                raise ValueError(f"Unsupported gate type: {gate.gtype}")
            value = fn(ins)
            if fault_node is not None and name == fault_node:
                value = fault_stuck_at
            values[name] = value
            del unresolved[name]
            progress = True
        if not progress:
            raise RuntimeError(
                "Combinational loop or unresolved signal inside one time frame; "
                "sequential ATPG requires an acyclic combinational part."
            )
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


def traces_diverge(good: list[CycleTrace], faulty: list[CycleTrace]) -> int | None:
    """First cycle index where a primary output differs, else None."""
    for i, (g, f) in enumerate(zip(good, faulty)):
        if g.primary_outputs != f.primary_outputs:
            return i
    return None
