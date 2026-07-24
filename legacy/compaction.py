class PatternCompactor:
    def __init__(self, sim, fault_model, circuit):
        self.sim = sim
        self.faults = fault_model.faults
        self.circuit = circuit

    def compact(self, patterns):
        covered = set()
        compacted = []

        for p in patterns:
            newly = set()
            for f in self.faults:
                if str(f) not in covered and self.sim.detects(p, f):
                    newly.add(str(f))

            if newly:
                compacted.append(p)
                covered |= newly

        return compacted, covered
