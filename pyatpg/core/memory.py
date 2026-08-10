import json
import os
import hashlib

class CircuitMemory:
    """
    Persistent memory for circuit analysis results.
    Stores results in a JSON file to skip redundant LLM/Atalanta runs.
    """
    def __init__(self, storage_path=None):
        if storage_path is None:
            # Default to outputs/knowledge_base.json relative to project root
            self.storage_path = os.path.join(os.getcwd(), 'outputs', 'knowledge_base.json')
        else:
            self.storage_path = storage_path
            
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self.memory = self._load()

    def _load(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save(self):
        with open(self.storage_path, 'w') as f:
            json.dump(self.memory, f, indent=2)

    def get_circuit_hash(self, circuit_text):
        """Generate a unique SHA-256 hash for the netlist text."""
        return hashlib.sha256(circuit_text.strip().encode('utf-8')).hexdigest()

    def find_match(self, circuit_text):
        """Check if we have analyzed this exact circuit before."""
        sig = self.get_circuit_hash(circuit_text)
        return self.memory.get(sig)

    def remember(self, circuit_text, analysis_results, stats=None):
        """Save analysis results to memory."""
        sig = self.get_circuit_hash(circuit_text)
        self.memory[sig] = {
            'timestamp': os.path.getmtime(self.storage_path) if os.path.exists(self.storage_path) else 0,
            'results': analysis_results,
            'stats': stats
        }
        self._save()

    def get_all_summaries(self):
        """Return a list of all remembered circuit names/stats for LLM context."""
        summaries = []
        for sig, data in self.memory.items():
            stats = data.get('stats', {})
            summaries.append({
                'name': stats.get('name', 'Unknown'),
                'gates': stats.get('gates', 0),
                'verdict_count': len(data.get('results', []))
            })
        return summaries
