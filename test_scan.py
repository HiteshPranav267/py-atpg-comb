import sys
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.graph_analysis import CircuitGraph

with open('benchmarks/scan_s27.v', 'r') as f:
    text = f.read()

parser = VerilogParser()
circuit = parser.parse(text)

print(f"Inputs: {circuit.inputs}")
print(f"Outputs: {circuit.outputs}")
print(f"Scan Flops: {[str(ff) for ff in circuit.flipflops]}")

cg = CircuitGraph(circuit)
features = cg.get_gate_features()
for node, f in features.items():
    if f['gate_type'] == 'SDFF':
        print(f"Node {node}: {f}")

