"""Diagnostic: find undetected faults and check if they're truly redundant."""
import random
random.seed(42)

from verilog_parser import VerilogParser
from scan_transform import ScanTransformer
from fault import FaultModel
from fault_simulator import FaultSimulator
from atpg_podem import PODEM
from atpg_hybrid import HybridATPG
from atpg_bruteforce import BruteForceATPG

with open('main.py') as f:
    content = f.read()
start = content.index('netlist = """') + len('netlist = """')
end = content.index('"""', start)
netlist = content[start:end]

parser = VerilogParser()
seq = parser.parse(netlist)
sc = ScanTransformer(seq).transform()
fm = FaultModel(sc)
fs = FaultSimulator(sc)

print(f"Inputs: {len(sc.inputs)}")
print(f"Outputs: {len(sc.outputs)}")
print(f"Gates: {len(sc.gates)}")
print(f"Total faults: {len(fm.faults)}")

podem = PODEM(sc)
bf = BruteForceATPG(sc)
hybrid = HybridATPG(podem, bf)
raw, detected, redundant = hybrid.generate(fm.faults, status_interval=500)

undetected = [f for f in fm.faults if str(f) not in detected]
print(f"\nUndetected faults ({len(undetected)}):")
for f in undetected:
    sig = f.signal
    is_pi = sig in sc.inputs
    is_gate = sig in sc.gates
    is_po = sig in sc.outputs
    print(f"  {f}  PI={is_pi}  gate={is_gate}  PO={is_po}")

print("\nBrute-force check on undetected (50000 random patterns)...")
for f in undetected:
    found = False
    for _ in range(50000):
        vec = tuple(random.randint(0, 1) for _ in range(len(sc.inputs)))
        if fs.detects(vec, f):
            print(f"  {f} -> DETECTABLE")
            found = True
            break
    if not found:
        print(f"  {f} -> TRULY REDUNDANT (undetectable)")
