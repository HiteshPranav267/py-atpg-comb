import sys
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.graph_analysis import CircuitGraph

with open('benchmarks/s35932.test_netlist.v', 'r') as f:
    text = f.read()

print("Parsing s35932...")
parser = VerilogParser()
circuit = parser.parse(text)

print(f"Inputs: {len(circuit.inputs)}")
print(f"Outputs: {len(circuit.outputs)}")
print(f"Total Flipflops: {len(circuit.flipflops)}")
print(f"Scan Flipflops: {len([ff for ff in circuit.flipflops if getattr(ff,'is_scan',False)])}")
print(f"Total Gates: {len(circuit.gates)}")

print("\nBuilding graph...")
cg = CircuitGraph(circuit)
stats = cg.get_stats()
print(f"Graph stats: {stats}")

print("\nSample SDFF features:")
features = cg.get_gate_features()
sdff_nodes = [(n, f) for n, f in features.items() if f['gate_type'] == 'SDFF']
print(f"SDFF nodes in graph: {len(sdff_nodes)}")
if sdff_nodes:
    n, f = sdff_nodes[0]
    print(f"Example: {n} => {f}")
