from fault_simulator import FaultSimulator
from collections import deque

# Five-valued logic: 0, 1, X (unknown), D (1/0), D' (0/1)
X = 'X'
D = 'D'
D_BAR = "D'"


def five_val_and(a, b):
    if a == 0 or b == 0:
        return 0
    if a == 1:
        return b
    if b == 1:
        return a
    if a == b:
        return a
    if {a, b} == {D, D_BAR}:
        return 0
    return X


def five_val_or(a, b):
    if a == 1 or b == 1:
        return 1
    if a == 0:
        return b
    if b == 0:
        return a
    if a == b:
        return a
    if {a, b} == {D, D_BAR}:
        return 1
    return X


def five_val_not(a):
    if a == 0:
        return 1
    if a == 1:
        return 0
    if a == D:
        return D_BAR
    if a == D_BAR:
        return D
    return X


def five_val_nand(inputs):
    r = 1
    for v in inputs:
        r = five_val_and(r, v)
    return five_val_not(r)


def five_val_nor(inputs):
    r = 0
    for v in inputs:
        r = five_val_or(r, v)
    return five_val_not(r)


def five_val_xor(a, b):
    if a == X or b == X:
        return X
    if a == 0:
        return b
    if b == 0:
        return a
    if a == 1:
        return five_val_not(b)
    if b == 1:
        return five_val_not(a)
    if a == b:
        return 0
    return 1


def eval_gate_5val(gate, values):
    inputs = [values.get(i, X) for i in gate.inputs]
    gtype = gate.gtype

    if gtype == "AND":
        r = 1
        for v in inputs:
            r = five_val_and(r, v)
        return r
    elif gtype == "OR":
        r = 0
        for v in inputs:
            r = five_val_or(r, v)
        return r
    elif gtype == "NAND":
        return five_val_nand(inputs)
    elif gtype == "NOR":
        return five_val_nor(inputs)
    elif gtype == "NOT":
        return five_val_not(inputs[0])
    elif gtype == "XOR":
        r = inputs[0]
        for v in inputs[1:]:
            r = five_val_xor(r, v)
        return r
    elif gtype == "BUF":
        return inputs[0]
    return X


def has_d(val):
    return val in (D, D_BAR)


class PODEM:
    def __init__(self, circuit):
        self.circuit = circuit
        self.n = len(circuit.inputs)
        self.input_list = list(circuit.inputs)
        self.output_list = list(circuit.outputs)
        self._topo_order = self._topological_sort()
        self._fanout_map = self._build_fanout_map()
        self.fault_sim = FaultSimulator(circuit)
        self.backtracks = 0
        self.max_backtracks = 200 * self.n

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
        for name in gate_names:
            if name not in order:
                order.append(name)
        return order

    def _build_fanout_map(self):
        fanout = {}
        for name, gate in self.circuit.gates.items():
            for inp in gate.inputs:
                fanout.setdefault(inp, []).append(name)
        return fanout

    def _imply(self, values, fault_signal, fault_stuck):
        """Forward implication with inline fault injection."""
        # Inject fault on PI BEFORE gate evaluation so D/D' propagates
        if fault_signal in self.input_list:
            pi_val = values.get(fault_signal, X)
            if pi_val == (1 - fault_stuck):
                values[fault_signal] = D if fault_stuck == 0 else D_BAR
            elif pi_val == fault_stuck:
                values[fault_signal] = fault_stuck

        for gate_name in self._topo_order:
            gate = self.circuit.gates[gate_name]
            val = eval_gate_5val(gate, values)

            if gate_name == fault_signal:
                # Inject fault at this gate's output
                if val == (1 - fault_stuck):
                    val = D if fault_stuck == 0 else D_BAR
                elif val == fault_stuck:
                    val = fault_stuck  # fault not excited
                # If val is X or D/D', keep as is
            values[gate_name] = val

    def _get_d_frontier(self, values):
        """Find gates whose output is X but have D/D' on at least one input."""
        frontier = []
        for gate_name in self._topo_order:
            if has_d(values.get(gate_name, X)):
                continue
            if values.get(gate_name, X) in (0, 1):
                continue
            gate = self.circuit.gates[gate_name]
            input_vals = [values.get(i, X) for i in gate.inputs]
            if any(has_d(v) for v in input_vals):
                frontier.append(gate_name)
        return frontier

    def _objective(self, fault_signal, fault_stuck, values):
        """Determine the next objective (signal, desired_value)."""
        fault_val = values.get(fault_signal, X)
        # If fault site not yet excited, activate it
        if not has_d(fault_val):
            if fault_val == X:
                return fault_signal, 1 - fault_stuck
            # fault_val == fault_stuck means fault is masked; need different inputs
            if fault_val == fault_stuck:
                # Need to change the fault site value — pick inputs to drive it
                if fault_signal in self.circuit.gates:
                    gate = self.circuit.gates[fault_signal]
                    for inp in gate.inputs:
                        if values.get(inp, X) == X:
                            nc = self._needed_value_to_excite(gate.gtype, fault_stuck)
                            return inp, nc
                return None, None

        # Fault is activated; propagate D/D' to output
        d_frontier = self._get_d_frontier(values)
        if not d_frontier:
            return None, None

        # Pick D-frontier gate closest to an output (highest topo level)
        levels = {name: i for i, name in enumerate(self._topo_order)}
        d_frontier.sort(key=lambda g: levels.get(g, 0), reverse=True)
        gate_name = d_frontier[0]
        gate = self.circuit.gates[gate_name]
        nc = self._non_controlling_value(gate.gtype)
        for inp in gate.inputs:
            val = values.get(inp, X)
            if val == X:
                return inp, nc
        return None, None

    def _needed_value_to_excite(self, gtype, fault_stuck):
        """What value do we need on an input to make gate output != fault_stuck."""
        if fault_stuck == 0:
            # Need output=1
            if gtype in ("AND", "BUF"):
                return 1
            if gtype in ("OR",):
                return 1
            if gtype in ("NAND",):
                return 0
            if gtype in ("NOR",):
                return 0
            if gtype == "NOT":
                return 0
        else:
            # Need output=0
            if gtype in ("AND",):
                return 0
            if gtype in ("OR", "BUF"):
                return 0
            if gtype in ("NAND",):
                return 1
            if gtype in ("NOR",):
                return 1
            if gtype == "NOT":
                return 1
        return 1

    def _non_controlling_value(self, gtype):
        if gtype in ("AND", "NAND"):
            return 1
        if gtype in ("OR", "NOR"):
            return 0
        return 1

    def _backtrace(self, objective_signal, objective_value, values):
        """Trace back from objective to a primary input assignment."""
        current = objective_signal
        value = objective_value

        while current not in self.input_list:
            if current not in self.circuit.gates:
                break
            gate = self.circuit.gates[current]
            gtype = gate.gtype

            # Inversion parity
            if gtype in ("NOT", "NAND", "NOR"):
                value = 1 - value

            chosen = None
            for inp in gate.inputs:
                if values.get(inp, X) == X:
                    chosen = inp
                    break
            if chosen is None:
                # All inputs assigned, can't backtrace further
                break
            current = chosen

        return current, value

    def _check_output_d(self, values):
        """Check if D or D' has reached any primary output."""
        for out in self.output_list:
            if has_d(values.get(out, X)):
                return True
        return False

    def _podem_recursive(self, fault_signal, fault_stuck, values):
        """Core recursive PODEM."""
        self._imply(values, fault_signal, fault_stuck)

        if self._check_output_d(values):
            return True

        # Check if test is impossible: D/D' exists but no D-frontier
        d_frontier = self._get_d_frontier(values)
        any_d = any(has_d(values.get(n, X))
                    for n in list(self.circuit.gates.keys()) + self.input_list)
        if any_d and not d_frontier and not self._check_output_d(values):
            return False

        obj_signal, obj_value = self._objective(fault_signal, fault_stuck, values)
        if obj_signal is None:
            return False

        pi, pi_value = self._backtrace(obj_signal, obj_value, values)
        if pi not in self.input_list:
            return False

        for try_val in [pi_value, 1 - pi_value]:
            self.backtracks += 1
            if self.backtracks > self.max_backtracks:
                return False

            saved = dict(values)
            values[pi] = try_val

            if self._podem_recursive(fault_signal, fault_stuck, values):
                return True

            values.clear()
            values.update(saved)

        values[pi] = X
        return False

    def generate_test(self, fault):
        """Generate a test vector for a given fault using PODEM."""
        self.backtracks = 0

        if isinstance(fault, tuple):
            fault_signal, fault_stuck = fault[0], fault[1]
        else:
            fault_signal, fault_stuck = fault.signal, fault.stuck

        values = {inp: X for inp in self.input_list}

        success = self._podem_recursive(fault_signal, fault_stuck, values)

        if success:
            vec = tuple(values.get(inp, 0) if values.get(inp, X) in (0, 1)
                        else 0 for inp in self.input_list)
            if self.fault_sim.detects(vec, fault):
                return vec
            # Try random fill for X positions
            import random
            x_positions = [i for i, inp in enumerate(self.input_list)
                           if values.get(inp, X) == X]
            for _ in range(30):
                v = list(vec)
                for pos in x_positions:
                    v[pos] = random.randint(0, 1)
                v = tuple(v)
                if self.fault_sim.detects(v, fault):
                    return v
        return None
