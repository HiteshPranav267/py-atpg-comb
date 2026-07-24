"""Test ATPG on all .bench benchmark files."""
import os
from netlist import NetlistParser
from scan_transform import ScanTransformer
from fault import FaultModel
from fault_simulator import FaultSimulator
from atpg_podem import PODEM
from atpg_bruteforce import BruteForceATPG
from atpg_hybrid import HybridATPG

BENCH_DIR = os.path.join(os.path.dirname(__file__), 'benchmarks')

for fname in sorted(os.listdir(BENCH_DIR)):
    if not fname.endswith('.bench'):
        continue
    fpath = os.path.join(BENCH_DIR, fname)
    with open(fpath) as f:
        text = f.read()

    parser = NetlistParser()
    seq = parser.parse(text)
    sc = ScanTransformer(seq).transform()
    fm = FaultModel(sc)
    fs = FaultSimulator(sc)

    podem = PODEM(sc)
    bf = BruteForceATPG(sc)
    hybrid = HybridATPG(podem, bf)

    print(f"\n{'='*50}")
    print(f"  {fname}: {len(sc.inputs)} inputs, {len(sc.outputs)} outputs, "
          f"{len(sc.gates)} gates, {len(fm.faults)} faults")
    print(f"{'='*50}")

    raw, detected, redundant = hybrid.generate(fm.faults, status_interval=500)
    coverage = 100 * len(detected) / len(fm.faults) if fm.faults else 0
    print(f"  RESULT: {len(detected)}/{len(fm.faults)} = {coverage:.1f}%")
