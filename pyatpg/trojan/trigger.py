from typing import List, Tuple
from pyatpg.core.netlist import Circuit

class CombinationalTriggerInserter:
    """Inserts combinational logic to serve as a Trojan trigger."""
    
    def __init__(self, gate_type: str = "AND"):
        self.gate_type = gate_type.upper()
        if self.gate_type not in ["AND", "OR", "NAND", "NOR", "XOR"]:
            raise ValueError(f"Unsupported trigger gate type: {gate_type}")

    def insert(self, circuit: Circuit, target_nets: List[str], trigger_out_name: str) -> Tuple[List[str], str]:
        """
        Inserts the trigger logic into the circuit.
        Returns the list of gates added and the output net name.
        """
        gate_name = f"HT_TRIGGER_{self.gate_type}"
        
        # We can simulate complex triggers by cascading gates if there are more than 2 inputs,
        # but for standard ISCAS parser, a single gate with multiple inputs is perfectly fine.
        circuit.add_gate(
            gtype=self.gate_type,
            output=trigger_out_name,
            inputs=target_nets
        )
        
        return [gate_name], trigger_out_name
