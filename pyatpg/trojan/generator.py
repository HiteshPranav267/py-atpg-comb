import os
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.netlist import NetlistParser
from pyatpg.trojan.inserter import TrojanInserter

class TrojanDatasetGenerator:
    """Generates a dataset of hardware trojans by wrapping a TrojanInserter."""
    
    def __init__(self, inserter: TrojanInserter, output_dir: str = "datasets"):
        self.inserter = inserter
        self.output_dir = output_dir

    def _parse_circuit(self, circuit_path: str):
        if not os.path.exists(circuit_path):
            raise FileNotFoundError(f"Base circuit not found: {circuit_path}")
            
        with open(circuit_path, 'r') as f:
            text = f.read()
            
        if circuit_path.endswith(('.v', '.verilog')):
            parser = VerilogParser()
        elif circuit_path.endswith('.bench'):
            parser = NetlistParser()
        else:
            raise ValueError("Unsupported base circuit format.")
            
        return parser.parse(text)

    def generate(self, circuit_path: str, variants: int = 100):
        """Generates variants of compromised circuits from the base circuit."""
        circuit_name = os.path.basename(circuit_path).split('.')[0]
        base_circuit = self._parse_circuit(circuit_path)
        
        # Directory Structure: datasets/<circuit>/
        ds_dir = os.path.join(self.output_dir, circuit_name)
        clean_dir = os.path.join(ds_dir, "clean")
        os.makedirs(clean_dir, exist_ok=True)
        
        # Save clean version (both Verilog and .bench formats)
        clean_verilog_path = os.path.join(clean_dir, "circuit.v")
        with open(clean_verilog_path, 'w') as f:
            f.write(base_circuit.to_verilog(module_name=circuit_name))

        clean_bench_path = os.path.join(clean_dir, "circuit.bench")
        with open(clean_bench_path, 'w') as f:
            f.write(base_circuit.to_bench())

        print(f"[TrojanDataset] Clean circuit exported to: {clean_verilog_path}")
        
        # Generate Trojans
        for i in range(1, variants + 1):
            trojan_dir = os.path.join(ds_dir, f"trojan_{i:04d}")
            os.makedirs(trojan_dir, exist_ok=True)
            
            try:
                # The inserter clones the circuit, so we pass the clean base_circuit repeatedly
                compromised, metadata = self.inserter.insert(
                    circuit=base_circuit,
                    trojan_id=i,
                    num_trigger_inputs=2
                )
                
                metadata.circuit = circuit_name
                
                # Export Verilog and .bench
                v_path = os.path.join(trojan_dir, "circuit.v")
                with open(v_path, 'w') as f:
                    f.write(compromised.to_verilog(module_name=circuit_name))

                bench_path = os.path.join(trojan_dir, "circuit.bench")
                with open(bench_path, 'w') as f:
                    f.write(compromised.to_bench())

                # Export JSON
                j_path = os.path.join(trojan_dir, "metadata.json")
                metadata.save(j_path)
                
                print(f"[TrojanDataset] Variant {i}/{variants} exported to {trojan_dir}")
                
            except Exception as e:
                print(f"[TrojanDataset] Error generating variant {i}: {e}")
