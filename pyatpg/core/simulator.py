class Simulator:
    def __init__(self, circuit):
        self.c = circuit

    def evaluate(self, vec):
        values = dict(zip(self.c.inputs, vec))
        unresolved = dict(self.c.gates)

        while unresolved:
            progress = False

            for name, g in list(unresolved.items()):
                try:
                    ins = [values[i] for i in g.inputs]
                except KeyError:
                    continue

                if g.gtype == "AND":
                    values[name] = int(all(ins))
                elif g.gtype == "OR":
                    values[name] = int(any(ins))
                elif g.gtype == "NAND":
                    values[name] = int(not all(ins))
                elif g.gtype == "NOR":
                    values[name] = int(not any(ins))
                elif g.gtype == "NOT":
                    values[name] = int(not ins[0])
                elif g.gtype == "BUF":
                    values[name] = ins[0]
                elif g.gtype == "XOR":
                    values[name] = int(sum(ins) % 2)
                else:
                    raise ValueError(f"Unsupported gate: {g.gtype}")

                del unresolved[name]
                progress = True

            if not progress:
                raise RuntimeError("Combinational loop or unresolved signal")

        return tuple(values[o] for o in self.c.outputs)
