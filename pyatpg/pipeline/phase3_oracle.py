import os
import platform
import zipfile
import subprocess
from pyatpg.exporters.atalanta_exporter import AtlantaExporter

class OracleExecutor:
    """Phase 3: Oracle Execution (The Actuator)"""
    
    def __init__(self, output_dir="./atalanta_jobs/"):
        self.output_dir = output_dir

    def execute(self, circuit, circuit_source, circuit_name, flagged_gates):
        """Execute Atalanta locally for the given circuit and targeted faults."""
        # Sanity check
        if len(circuit.flipflops) > 0:
            raise ValueError("py-atpg requires combinational circuits. Sequential elements detected.")
            
        print(f"[Phase 3] Generating Atalanta Scripts for {circuit_name}...")
        exporter = AtlantaExporter(circuit_name=circuit_name)
        
        # Format flagged gates for exporter
        formatted_gates = [{'gate_id': g} for g in flagged_gates]
        
        exporter.generate_scripts(
            circuit_source, 
            formatted_gates, 
            circuit=circuit, 
            source_name=f"{circuit_name}.v"
        )
        
        zip_path = exporter.export_bundle(output_dir=self.output_dir)
        print(f"[Phase 3] Job Bundle created at: {zip_path}")
        
        print("[Phase 3] Unzipping and running Atalanta locally...")
        extract_dir = zip_path.replace('.zip', '')
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)

        # The exporter emits a .bat + Atalanta.exe on Windows and a .sh +
        # atalanta ELF binary elsewhere, so invoke whichever it actually wrote.
        # Pass an absolute script path -- cmd won't resolve a bare filename
        # from cwd, and it keeps the bash side independent of PATH quirks.
        if platform.system() == "Windows":
            script_path = os.path.abspath(os.path.join(extract_dir, 'run_atalanta.bat'))
            command = ['cmd', '/c', script_path]
        else:
            script_path = os.path.abspath(os.path.join(extract_dir, 'run_atalanta.sh'))
            os.chmod(script_path, 0o755)
            command = ['bash', script_path]

        result = subprocess.run(command, cwd=extract_dir, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Error executing Atalanta:\n{result.stderr or result.stdout}")
            
        print(result.stdout)
        print(f"[Success] Local Execution complete. Logs saved to: {extract_dir}")
        return extract_dir, result.stdout
