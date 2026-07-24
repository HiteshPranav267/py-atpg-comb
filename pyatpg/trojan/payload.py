from typing import List, Tuple
from pyatpg.core.netlist import Circuit

class CombinationalPayloadInserter:
    """Inserts a payload gate into a target net to compromise the circuit."""
    
    def __init__(self, gate_type: str = "XOR"):
        self.gate_type = gate_type.upper()
        if self.gate_type not in ["XOR", "OR", "AND", "MUX"]:
            raise ValueError(f"Unsupported payload gate type: {gate_type}")

    def insert(self, circuit: Circuit, target_net: str, trigger_net: str) -> Tuple[List[str], str]:
        """
        Inserts the payload logic into the circuit.
        Modifies the target net in-place in the graph structure.
        """
        gate_name = f"HT_PAYLOAD_{self.gate_type}"
        
        # We must intercept `target_net`.
        # To do this structurally:
        # 1. Rename the original driver's output from `target_net` to `target_net_orig`.
        # 2. Insert the Payload Gate: Output = `target_net`, Inputs = [`target_net_orig`, `trigger_net`]
        
        if target_net not in circuit.gates and target_net not in circuit.inputs:
            raise ValueError(f"Target net {target_net} not found in circuit.")
            
        orig_net_name = f"{target_net}_orig"
        
        # If the target is driven by a gate, rename the gate's output
        if target_net in circuit.gates:
            orig_gate = circuit.gates[target_net]
            orig_gate.output = orig_net_name
            # Move the gate to the new key in the dict
            del circuit.gates[target_net]
            circuit.gates[orig_net_name] = orig_gate
            
            # Since the original gate was modified, its output is now `orig_net_name`
            # The downstream gates that consumed `target_net` will now consume the payload's output `target_net`.
            # So downstream connections are automatically preserved!
        elif target_net in circuit.inputs:
            # If targeting a primary input directly, rename the input entirely or replace its usages.
            # In structural verilog, this means creating an intermediate wire.
            # We'll just rename the input list.
            circuit.inputs.remove(target_net)
            circuit.inputs.add(orig_net_name)
            circuit.nodes.add(orig_net_name)
            
            # Update all gates that used target_net to use target_net?
            # No, if we insert a gate that outputs `target_net`, the existing downstream gates are fine!
            # Wait, primary inputs are driven by external pins. The external pin is `orig_net_name`.
            # But the module signature will change if we rename inputs. 
            # It's safer to avoid targeting primary inputs, as handled in the strategy class.

        # Add the payload gate
        circuit.add_gate(
            gtype=self.gate_type,
            output=target_net,
            inputs=[orig_net_name, trigger_net]
        )
        
        return [gate_name], target_net
