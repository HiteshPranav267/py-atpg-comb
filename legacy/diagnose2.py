"""Deep check: why C_16_SA_1 and C_13_SA_1 are undetectable."""
from verilog_parser import VerilogParser
from scan_transform import ScanTransformer
from fault_simulator import FaultSimulator
from fault import Fault

with open('main.py') as f:
    content = f.read()
start = content.index('netlist = """') + len('netlist = """')
end = content.index('"""', start)
netlist = content[start:end]

parser = VerilogParser()
seq = parser.parse(netlist)
sc = ScanTransformer(seq).transform()
fs = FaultSimulator(sc)

# Trace C_16's cone: what gates does C_16 feed into?
print("=== C_16 fanout cone ===")
for name, gate in sc.gates.items():
    if 'C_16' in gate.inputs:
        print(f"  {name} = {gate.gtype}({gate.inputs})")

print("\n=== C_13 fanout cone ===")
for name, gate in sc.gates.items():
    if 'C_13' in gate.inputs:
        print(f"  {name} = {gate.gtype}({gate.inputs})")

# Full exhaustive test for C_16_SA_1 and C_13_SA_1 with 200000 random
import random
random.seed(123)
n = len(sc.inputs)
for sig in ['C_16', 'C_13']:
    fault = Fault(sig, 1)
    found = False
    for i in range(200000):
        vec = tuple(random.randint(0, 1) for _ in range(n))
        if fs.detects(vec, fault):
            print(f"{sig}_SA_1 detected by pattern {i}")
            found = True
            break
    if not found:
        print(f"{sig}_SA_1 NOT detected in 200000 patterns - truly redundant")

# Check which inputs are NOT used by any gate
gate_inputs_used = set()
for name, gate in sc.gates.items():
    for inp in gate.inputs:
        gate_inputs_used.add(inp)

unused_inputs = set(sc.inputs) - gate_inputs_used
print(f"\nInputs not used by any gate: {unused_inputs}")

# Check inputs used only in AND gate chains where they're always masked
print("\nChecking CK connections:")
print(f"  CK in inputs: {'CK' in sc.inputs}")
print(f"  CK in gate inputs: {'CK' in gate_inputs_used}")
