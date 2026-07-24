from .phase1_structural import StructuralAnalyzer
from .phase2_ai import AIPrioritizer
from .phase3_oracle import OracleExecutor
from .phase4_validation import Validator
import os

class PyATPGPipeline:
    """Master orchestrator for the PyATPG pipeline."""
    
    def __init__(self, api_key=None, output_dir="./modus_jobs/"):
        self.phase1 = StructuralAnalyzer()
        self.phase2 = AIPrioritizer(api_key=api_key)
        self.phase3 = OracleExecutor(output_dir=output_dir)
        self.phase4 = Validator()
        self.output_dir = output_dir
        
    def run(self, netlist_path, patterns=3000):
        """Execute the full end-to-end pipeline in memory."""
        # --- Phase 1 ---
        circuit, circuit_source, circuit_name = self.phase1.parse_circuit(filepath=netlist_path)
        
        # Clean up the name for files
        clean_name = circuit_name.replace('.v', '').replace('.bench', '')
        if clean_name.endswith('_mod'):
            clean_name = clean_name[:-4]
            
        phase1_data = self.phase1.analyze(circuit, patterns=patterns)
        
        # --- Phase 2 ---
        suspicious_gates = self.phase2.prioritize(
            profiles=phase1_data['profiles'],
            summary=phase1_data['llm_summary']
        )
        
        # --- Phase 3 ---
        extract_dir, _ = self.phase3.execute(
            circuit=circuit,
            circuit_source=circuit_source,
            circuit_name=clean_name,
            flagged_gates=suspicious_gates
        )
        
        # --- Phase 4 ---
        targeted_log = os.path.join(extract_dir, "targeted_atalanta.log")
        baseline_log = os.path.join(extract_dir, "baseline_atalanta.log")
        fault_detail_log = os.path.join(extract_dir, f"{clean_name}.ufaults")
        
        report = self.phase4.validate(
            targeted_log=targeted_log,
            baseline_log=baseline_log,
            fault_detail_log=fault_detail_log,
            ai_suspects=suspicious_gates,
            profiles=phase1_data['profiles'],
            circuit_stats=phase1_data['statistics']
        )
        
        return report

__all__ = [
    'PyATPGPipeline',
    'StructuralAnalyzer',
    'AIPrioritizer',
    'OracleExecutor',
    'Validator'
]
