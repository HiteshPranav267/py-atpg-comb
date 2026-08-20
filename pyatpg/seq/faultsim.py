"""Sequential fault list construction and coverage calculation.

Coverage is always computed here, by running :mod:`simulator` (the
deterministic engine) -- never estimated, never taken on the word of PODEM
or an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from pyatpg.core.netlist import Circuit

from .models import SequentialFault, SequentialResults, TestSequence
from .simulator import simulate, traces_diverge


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
    faulty = simulate(circuit, sequence.vectors, fault_node=fault.node, fault_stuck_at=fault.stuck_at)
    return traces_diverge(good, faulty)


def evaluate_coverage(
    circuit: Circuit,
    faults: list[SequentialFault],
    sequences: list[TestSequence],
) -> tuple[SequentialResults, list[DetectionRecord], list[SequentialFault], list[list[str]]]:
    """Simulate every sequence against every undetected fault until covered.

    Returns (results, detection records, undetected faults, per-sequence
    newly-detected-fault-name lists) -- the last is what the LLM feedback
    loop sends back as a delta instead of the whole history.
    """
    remaining = list(faults)
    detections: list[DetectionRecord] = []
    newly_detected_by_sequence: list[list[str]] = [[] for _ in sequences]

    good_traces = [simulate(circuit, seq.vectors) for seq in sequences]

    for fault in faults:
        for seq_idx, seq in enumerate(sequences):
            faulty = simulate(
                circuit, seq.vectors, fault_node=fault.node, fault_stuck_at=fault.stuck_at
            )
            cycle = traces_diverge(good_traces[seq_idx], faulty)
            if cycle is not None:
                detections.append(DetectionRecord(fault=fault, sequence_index=seq_idx, cycle=cycle))
                newly_detected_by_sequence[seq_idx].append(fault.name)
                break

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
