from circuit import Circuit

class ScanTransformer:
    def __init__(self, circuit):
        self.c = circuit

    def transform(self):
        sc = Circuit()

        for i in self.c.inputs:
            sc.add_input(i)

        # Scan-based ATPG model:
        #   Q = pseudo-PI  (scan chain loads DFF state)
        #   D = pseudo-PO  (scan chain captures combinational result)
        dffs = self.c.dffs
        if isinstance(dffs, dict):
            for q, d in dffs.items():
                sc.add_input(q)
                sc.add_output(d)
        elif isinstance(dffs, list):
            for ff in dffs:
                sc.add_input(ff.q)
                sc.add_output(ff.d)

        for o in self.c.outputs:
            sc.add_output(o)

        for name, g in self.c.gates.items():
            sc.add_gate(name, g.gtype, g.inputs)

        return sc
