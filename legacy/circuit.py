# circuit.py

class Gate:
    def __init__(self, name, gtype, inputs):
        self.name = name
        self.gtype = gtype
        self.inputs = inputs


class Circuit:
    def __init__(self):
        self.inputs = []
        self.outputs = []
        self.gates = {}
        self.dffs = {}

    def add_input(self, name):
        if name not in self.inputs:
            self.inputs.append(name)

    def add_output(self, name):
        if name not in self.outputs:
            self.outputs.append(name)

    def add_gate(self, name, gtype, inputs):
        self.gates[name] = Gate(name, gtype, inputs)

    def add_dff(self, q, d):
        self.dffs[q] = d
