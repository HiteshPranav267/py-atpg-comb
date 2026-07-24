class FaultSimulator:
    def __init__(self, circuit):
        self.circuit = circuit
        self.input_list = list(circuit.inputs)
        self._topo_order = self._topological_sort()

    # -------------------------------------------------
    # Topological sort for correct evaluation order
    # -------------------------------------------------
    def _topological_sort(self):
        gate_names = set(self.circuit.gates.keys())
        in_degree = {name: 0 for name in gate_names}
        dependents = {name: [] for name in gate_names}

        for name, gate in self.circuit.gates.items():
            for inp in gate.inputs:
                if inp in gate_names:
                    in_degree[name] += 1
                    dependents[inp].append(name)

        queue = [name for name, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            node = queue.pop(0)
            order.append(node)
            for dep in dependents[node]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        # If some gates remain (cycle), append them at the end
        for name in gate_names:
            if name not in order:
                order.append(name)

        return order

    # -------------------------------------------------
    # Gate evaluation
    # -------------------------------------------------
    def eval_gate(self, gate, values):
        inputs = [values.get(i, 0) for i in gate.inputs]
        gtype = gate.gtype

        if gtype == "AND":
            return int(all(inputs))

        elif gtype == "OR":
            return int(any(inputs))

        elif gtype == "NAND":
            return int(not all(inputs))

        elif gtype == "NOR":
            return int(not any(inputs))

        elif gtype == "NOT":
            return int(not inputs[0])

        elif gtype == "XOR":
            result = inputs[0]
            for v in inputs[1:]:
                result ^= v
            return int(result)

        elif gtype == "BUF":
            return inputs[0]

        else:
            raise ValueError(f"Unknown gate type: {gtype}")

    # -------------------------------------------------
    # Pattern normalization
    # -------------------------------------------------
    def normalize_pattern(self, pattern):
        if isinstance(pattern, dict):
            return pattern

        return {
            self.input_list[i]: pattern[i]
            for i in range(min(len(pattern), len(self.input_list)))
        }

    # -------------------------------------------------
    # Fault unpacking
    # -------------------------------------------------
    def unpack_fault(self, fault):
        if fault is None:
            return None, None

        if isinstance(fault, tuple):
            return fault[0], fault[1]

        return fault.signal, fault.stuck

    # -------------------------------------------------
    # Circuit simulation (topologically ordered)
    # -------------------------------------------------
    def simulate(self, pattern, fault=None):
        values = {}
        pattern = self.normalize_pattern(pattern)

        fault_signal, fault_value = self.unpack_fault(fault)

        # set inputs
        for inp in self.circuit.inputs:
            values[inp] = pattern.get(inp, 0)

        # inject input fault
        if fault_signal and fault_signal in values:
            values[fault_signal] = fault_value

        # evaluate gates in topological order
        for gate_name in self._topo_order:
            gate = self.circuit.gates[gate_name]
            out = self.eval_gate(gate, values)

            if fault_signal and gate_name == fault_signal:
                out = fault_value

            values[gate_name] = out

        # collect outputs
        outputs = {}
        for out in self.circuit.outputs:
            outputs[out] = values.get(out, 0)

        return outputs

    # -------------------------------------------------
    # Bulk fault simulation (detect many faults at once)
    # -------------------------------------------------
    def simulate_faults_bulk(self, pattern, faults):
        """Simulate one pattern against many faults, return set of detected fault strings."""
        good = self.simulate(pattern, None)
        pattern_norm = self.normalize_pattern(pattern)
        detected = set()

        # Pre-compute good circuit values for cone-skipping
        good_values = {}
        for inp in self.circuit.inputs:
            good_values[inp] = pattern_norm.get(inp, 0)
        for gate_name in self._topo_order:
            gate = self.circuit.gates[gate_name]
            good_values[gate_name] = self.eval_gate(gate, good_values)

        for fault in faults:
            fault_signal, fault_value = self.unpack_fault(fault)

            # Skip if fault site already has the stuck-at value in good circuit
            if good_values.get(fault_signal) == fault_value:
                continue

            bad = self.simulate(pattern, fault)
            if good != bad:
                detected.add(str(fault))
        return detected

    # -------------------------------------------------
    # Fault detection
    # -------------------------------------------------
    def detects_fault(self, pattern, fault):
        good = self.simulate(pattern, None)
        bad = self.simulate(pattern, fault)
        return good != bad

    def detects(self, pattern, fault):
        return self.detects_fault(pattern, fault)
