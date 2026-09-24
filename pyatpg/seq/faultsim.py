"""Sequential fault list construction and coverage calculation.

Coverage is always computed here, by running :mod:`simulator` (the
deterministic engine) -- never estimated, never taken on the word of PODEM
or an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyatpg.core.netlist import Circuit

from .models import SequentialFault, SequentialResults, TestSequence
from .simulator import _gate_order, first_divergence, simulate


def checkpoint_fault_list(circuit: Circuit) -> list[SequentialFault]:
    """Stuck-at-0/1 at every gate output, reachable primary input, and flip-flop Q.

    This is a *checkpoint* fault list (every net that can independently
    carry a fault effect), not an equivalence-collapsed one -- collapsing
    requires structural analysis this engine does not yet do, so the list is
    slightly larger than a fully collapsed one rather than silently
    understating what was checked.

    A primary input that drives no gate and no flip-flop D input is excluded:
    it cannot activate anything, so a stuck-at fault on it is permanently
    undetectable by construction, not because the search failed. Left in,
    such nodes (typically a clock port declared as a plain `input` in
    structural Verilog, e.g. `input CK, ...` with no `always @(posedge CK)`
    modeled here -- clocking is implicit in the per-cycle simulation model
    instead) would silently cap fault coverage below 100% on an otherwise
    fully-covered circuit.
    """
    referenced: set[str] = set()
    for gate in circuit.gates.values():
        referenced.update(gate.inputs)
    for ff in circuit.flipflops:
        referenced.add(ff.d)

    reachable_inputs = set(circuit.inputs) & referenced
    nodes: set[str] = set(circuit.gates.keys()) | reachable_inputs
    nodes |= {ff.q for ff in circuit.flipflops}
    faults = []
    for node in sorted(nodes):
        faults.append(SequentialFault(node=node, stuck_at=0))
        faults.append(SequentialFault(node=node, stuck_at=1))
    return faults


@dataclass
class DetectionRecord:
    fault: SequentialFault
    sequence_index: int
    cycle: int


def detects(circuit: Circuit, fault: SequentialFault, sequence: TestSequence) -> int | None:
    """Cycle index at which ``sequence`` detects ``fault``, else None."""
    good = simulate(circuit, sequence.vectors)
    return first_divergence(circuit, sequence.vectors, good, fault.node, fault.stuck_at)


_OPS = {"AND": 0, "OR": 1, "NAND": 2, "NOR": 3, "NOT": 4, "BUF": 5, "BUFF": 5, "XOR": 6}


def _parallel_first_detection(
    circuit: Circuit,
    vectors: list[dict[str, int]],
    faults: list[SequentialFault],
) -> dict[int, int]:
    """Detection cycle of each fault under one sequence, all faults at once.

    Parallel-fault simulation: bit 0 of every net's integer value is the
    fault-free machine and bit ``k`` (k >= 1) is the machine carrying
    ``faults[k-1]``, so one bitwise pass over the gates per clock cycle
    simulates every faulty machine together. Semantics match the serial
    ``first_divergence``: a stuck PI/flip-flop Q is forced before the gates
    are evaluated, a stuck gate output is forced as soon as it is computed,
    flip-flops reset to 0, and a fault is detected at the first cycle where
    any primary output differs from the fault-free machine.

    Returns {fault index: detection cycle} for the detected faults only.
    """
    width = len(faults) + 1
    mask = (1 << width) - 1
    force_clear: dict[str, int] = {}
    force_set: dict[str, int] = {}
    for k, fault in enumerate(faults, start=1):
        bit = 1 << k
        force_clear[fault.node] = force_clear.get(fault.node, 0) | bit
        if fault.stuck_at:
            force_set[fault.node] = force_set.get(fault.node, 0) | bit

    def force(node, value):
        clear = force_clear.get(node)
        if clear is None:
            return value
        return (value & ~clear) | force_set.get(node, 0)

    order = [(name, _OPS[fn_name], inputs, name in force_clear)
             for name, fn_name, inputs in _ops_order(circuit)]
    outputs = list(circuit.outputs)
    ffs = circuit.flipflops

    state = {ff.q: force(ff.q, 0) for ff in ffs}
    detected = 0
    cycles: dict[int, int] = {}
    for cycle, vector in enumerate(vectors):
        values = {pi: force(pi, mask if v else 0) for pi, v in vector.items()}
        for q, v in state.items():
            values[q] = force(q, v)
        for name, op, inputs, faulted in order:
            if op == 0:
                v = mask
                for i in inputs:
                    v &= values[i]
            elif op == 1:
                v = 0
                for i in inputs:
                    v |= values[i]
            elif op == 2:
                v = mask
                for i in inputs:
                    v &= values[i]
                v ^= mask
            elif op == 3:
                v = 0
                for i in inputs:
                    v |= values[i]
                v ^= mask
            elif op == 4:
                v = values[inputs[0]] ^ mask
            elif op == 5:
                v = values[inputs[0]]
            else:
                v = 0
                for i in inputs:
                    v ^= values[i]
            if faulted:
                v = force(name, v)
            values[name] = v
        diff = 0
        for po in outputs:
            v = values[po]
            diff |= v ^ (mask if v & 1 else 0)
        new = diff & ~detected & ~1
        if new:
            detected |= new
            k = 1
            while new >> k:
                if (new >> k) & 1:
                    cycles[k - 1] = cycle
                k += 1
            if detected | 1 == mask:
                break
        state = {ff.q: values[ff.d] for ff in ffs}
    return cycles


def _ops_order(circuit: Circuit) -> list[tuple[str, str, list[str]]]:
    """Topological gate order with gate type names (for the bit-parallel simulator)."""
    return [(name, circuit.gates[name].gtype, inputs)
            for name, _fn, inputs in _gate_order(circuit)]


def evaluate_coverage(
    circuit: Circuit,
    faults: list[SequentialFault],
    sequences: list[TestSequence],
) -> tuple[SequentialResults, list[DetectionRecord], list[SequentialFault], list[list[str]]]:
    """Simulate every sequence against every undetected fault until covered.

    Returns (results, detection records, undetected faults, per-sequence
    newly-detected-fault-name lists) -- the last is what the LLM feedback
    loop sends back as a delta instead of the whole history.

    Each fault is credited to the first sequence (in order) that detects it,
    at that sequence's first diverging cycle -- sequences are simulated in
    order with only the still-undetected faults, in parallel.
    """
    newly_detected_by_sequence: list[list[str]] = [[] for _ in sequences]
    found: dict[int, DetectionRecord] = {}
    pending = list(range(len(faults)))

    for seq_idx, seq in enumerate(sequences):
        if not pending:
            break
        batch = [faults[i] for i in pending]
        hits = _parallel_first_detection(circuit, seq.vectors, batch)
        still = []
        for pos, fault_idx in enumerate(pending):
            if pos in hits:
                found[fault_idx] = DetectionRecord(
                    fault=faults[fault_idx], sequence_index=seq_idx, cycle=hits[pos]
                )
                newly_detected_by_sequence[seq_idx].append(faults[fault_idx].name)
            else:
                still.append(fault_idx)
        pending = still

    detections = [found[i] for i in sorted(found)]
    detected_names = {d.fault.name for d in detections}
    undetected = [f for f in faults if f.name not in detected_names]

    total = len(faults)
    detected = len(detected_names)
    results = SequentialResults(
        total_faults=total,
        detected_faults=detected,
        undetected_faults=total - detected,
        aborted_faults=0,
        fault_coverage=round(100.0 * detected / total, 3) if total else 0.0,
        sequence_count=len(sequences),
        total_clock_cycles=sum(s.cycles for s in sequences),
        max_sequence_length=max((s.cycles for s in sequences), default=0),
    )
    return results, detections, undetected, newly_detected_by_sequence
