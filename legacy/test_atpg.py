from netlist import NetlistParser
from fault import FaultModel
from fault_simulator import FaultSimulator
from atpg_podem import PODEM
from atpg_bruteforce import BruteForceATPG
from atpg_hybrid import HybridATPG
from scan_transform import ScanTransformer

def test_c17():
    c17_netlist = """
    INPUT(1)
    INPUT(2)
    INPUT(3)
    INPUT(6)
    INPUT(7)
    OUTPUT(22)
    OUTPUT(23)
    10 = NAND(1, 3)
    11 = NAND(3, 6)
    16 = NAND(2, 11)
    19 = NAND(11, 7)
    22 = NAND(10, 16)
    23 = NAND(16, 19)
    """
    parser = NetlistParser()
    circuit = parser.parse(c17_netlist)
    scan_circuit = ScanTransformer(circuit).transform()
    
    fm = FaultModel(scan_circuit)
    podem = PODEM(scan_circuit)
    brute = BruteForceATPG(scan_circuit)
    hybrid = HybridATPG(podem, brute)
    
    raw_patterns, detected, redundant = hybrid.generate(fm.faults)
    
    all_faults = {str(f) for f in fm.faults}
    final_detected = detected & all_faults
    coverage = 100 * len(final_detected) / len(all_faults) if all_faults else 0
    print(f"Total faults: {len(all_faults)}")
    print(f"Detected: {len(final_detected)}")
    print(f"Coverage: {coverage:.1f}%")

if __name__ == "__main__":
    test_atpg_py = "test_atpg.py"
    test_c17()
