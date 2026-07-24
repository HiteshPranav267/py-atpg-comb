import os
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.netlist import NetlistParser
from pyatpg.core.graph_analysis import CircuitGraph

class StructuralAnalyzer:
    """Phase 1: Structural Analysis Engine"""

    def parse_circuit(self, filepath=None, source_text=None, filename="unnamed.v"):
        """Parse circuit text based on extension or provided text."""
        if filepath:
            if not os.path.exists(filepath):
                raise FileNotFoundError(f"File not found: {filepath}")
            with open(filepath, 'r') as f:
                source_text = f.read()
            filename = os.path.basename(filepath)

        if not source_text:
            raise ValueError("Must provide either filepath or source_text")

        if filename.endswith(('.v', '.verilog')):
            parser = VerilogParser()
        elif filename.endswith('.bench'):
            parser = NetlistParser()
        else:
            raise ValueError("Unsupported file format. Please provide a .v or .bench file.")
            
        print(f"[Phase 1] Loading netlist: {filename}")
        return parser.parse(source_text), source_text, filename

    def analyze(self, circuit, patterns=3000):
        """Analyze the parsed circuit, compute SCOAP and topological metrics, and simulate patterns."""
        print(f"[Phase 1] Building graph representation...")
        cg = CircuitGraph(circuit)
        
        print(f"[Phase 1] Calculating SCOAP and Topological Metrics...")
        stats = cg.get_stats()
        
        print(f"[Phase 1] Simulating {patterns} random patterns for Toggle Rates (this may take a moment)...")
        cg.compute_toggle_rates(num_patterns=patterns)
        
        print(f"[Phase 1] Generating Feature Context for LLM...")
        llm_profiles = cg.to_llm_feature_profiles()
        llm_summary = cg.generate_circuit_summary_for_llm()
        
        print(f"[Success] Generated contextual features for {len(llm_profiles)} gates.")
        
        return {
            "circuit": circuit,
            "graph": cg,
            "statistics": stats,
            "llm_summary": llm_summary,
            "profiles": llm_profiles
        }
