from itertools import product
from fault_simulator import FaultSimulator

class BruteForceATPG:
    def __init__(self, circuit):
        self.sim = FaultSimulator(circuit)
        self.n = len(circuit.inputs)

    def generate_test(self, fault):
        for vec in product([0,1], repeat=self.n):
            if self.sim.detects(vec, fault):
                return vec
        return None
