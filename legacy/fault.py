class Fault:
    def __init__(self, signal, stuck):
        self.signal = signal
        self.stuck = stuck

    def __str__(self):
        return f"{self.signal}_SA_{self.stuck}"

    def __eq__(self, other):
        return isinstance(other, Fault) and self.signal == other.signal and self.stuck == other.stuck

    def __hash__(self):
        return hash((self.signal, self.stuck))


class FaultModel:
    def __init__(self, circuit):
        self.faults = []
        signals = set()

        # Find which signals are actually used by gates
        gate_inputs_used = set()
        for g in circuit.gates.values():
            for inp in g.inputs:
                gate_inputs_used.add(inp)

        # Primary inputs (only those connected to at least one gate)
        for s in circuit.inputs:
            if s in gate_inputs_used:
                signals.add(s)

        # Gate outputs
        for s in circuit.gates.keys():
            signals.add(s)

        # Primary outputs (may overlap with gate outputs)
        for s in circuit.outputs:
            signals.add(s)

        # Create SA0 and SA1 for each signal
        for s in signals:
            self.faults.append(Fault(s, 0))
            self.faults.append(Fault(s, 1))
