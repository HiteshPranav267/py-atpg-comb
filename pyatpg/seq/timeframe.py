"""Time-frame expansion (the iterative array model) for sequential ATPG.

A sequential circuit with flip-flops is unrolled into ``n_frames`` copies of
its combinational logic, reusing the existing ``Circuit``/``Gate`` classes
so the rest of the codebase (SCOAP, the simulator, PODEM) never needs a
second circuit representation:

    frame 0:  FF outputs (Q) are free pseudo-primary-inputs
    frame k>0: FF outputs (Q) are aliased to frame k-1's FF input (D) net

Aliasing frame 0's FF outputs as free inputs -- rather than adding backward
("negative") time frames to justify a reachable reset state -- is a
deliberate, documented simplification: it treats the initial state as
directly controllable, the same assumption full-scan ATPG makes about a
scan-loaded state. It lets PODEM search forward-only, which is what makes a
from-scratch sequential ATPG search tractable here. It is *not* trusted as
truth: :mod:`simulator` always re-verifies any candidate sequence from a
fixed, real power-on reset state (all flip-flops at 0), and only sequences
that actually detect something under that real simulation are kept. See
``engine.py`` for how the two are reconciled.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyatpg.core.netlist import Circuit


@dataclass
class UnrolledCircuit:
    """A flat combinational circuit built from ``n_frames`` copies."""

    circuit: Circuit
    n_frames: int
    #: unrolled PI name -> (frame, original PI name); only real PIs, not the
    #: frame-0 pseudo-PIs standing in for initial flip-flop state.
    pi_frames: dict[str, tuple[int, str]]
    #: unrolled pseudo-PI name -> (frame, original FF Q name); frame is
    #: always 0 (only frame 0 has free state pseudo-PIs).
    state_pseudo_inputs: dict[str, str]
    #: unrolled PO name -> (frame, original PO name).
    po_frames: dict[str, tuple[int, str]]


def _resolve(ff_d_of_q: dict[str, str], frame: int, node: str) -> str:
    if node in ff_d_of_q:
        if frame == 0:
            return f"f0_{node}"
        return _resolve(ff_d_of_q, frame - 1, ff_d_of_q[node])
    return f"f{frame}_{node}"


def is_combinational_part_acyclic(circuit: Circuit) -> bool:
    """Can every gate be levelized within a single time frame?

    ``CircuitGraph.get_stats()["is_dag"]`` is the wrong check here: it adds
    an edge for every flip-flop's D->Q transition *and* an edge for Q's
    fanout, so any register that feeds logic which (eventually) feeds that
    same register's own D input -- i.e. essentially every real sequential
    circuit with feedback -- reads as "cyclic", because that graph does not
    distinguish "next cycle" from "this cycle". It was never wrong for its
    original purpose (it only ever ran on already-verified-combinational
    circuits), but it cannot be reused for the question this engine actually
    needs answered: treating every flip-flop Q as a free, already-known
    value (a pseudo primary input) and every flip-flop D as a plain sink,
    can the remaining combinational gates be topologically ordered? If not,
    there is a real, non-clocked (asynchronous/latch-style) feedback loop
    that time-frame expansion cannot handle.
    """
    resolved = set(circuit.inputs) | {ff.q for ff in circuit.flipflops}
    unresolved = dict(circuit.gates)
    while unresolved:
        progress = False
        for name, gate in list(unresolved.items()):
            if all(i in resolved for i in gate.inputs):
                resolved.add(name)
                del unresolved[name]
                progress = True
        if not progress:
            return False
    return True


def build_unrolled_circuit(circuit: Circuit, n_frames: int) -> UnrolledCircuit:
    """Build the ``n_frames``-deep iterative array for ``circuit``.

    Every original primary output is exposed in every frame (an unrolled PO
    per frame), because a sequential fault may only become observable on a
    later cycle -- there is no single "the" output the way a purely
    combinational circuit has.
    """
    if n_frames < 1:
        raise ValueError("n_frames must be >= 1")

    ff_d_of_q = {ff.q: ff.d for ff in circuit.flipflops}
    unrolled = Circuit()
    pi_frames: dict[str, tuple[int, str]] = {}
    state_pseudo_inputs: dict[str, str] = {}
    po_frames: dict[str, tuple[int, str]] = {}

    for frame in range(n_frames):
        for pi in circuit.inputs:
            name = _resolve(ff_d_of_q, frame, pi)
            unrolled.add_input(name)
            pi_frames[name] = (frame, pi)

        if frame == 0:
            for ff in circuit.flipflops:
                name = _resolve(ff_d_of_q, 0, ff.q)
                unrolled.add_input(name)
                state_pseudo_inputs[name] = ff.q

        for out_name, gate in circuit.gates.items():
            new_inputs = [_resolve(ff_d_of_q, frame, i) for i in gate.inputs]
            new_output = _resolve(ff_d_of_q, frame, out_name)
            unrolled.add_gate(gate.gtype, new_output, new_inputs)

        for po in circuit.outputs:
            name = _resolve(ff_d_of_q, frame, po)
            unrolled.add_output(name)
            po_frames[name] = (frame, po)

    return UnrolledCircuit(
        circuit=unrolled,
        n_frames=n_frames,
        pi_frames=pi_frames,
        state_pseudo_inputs=state_pseudo_inputs,
        po_frames=po_frames,
    )
