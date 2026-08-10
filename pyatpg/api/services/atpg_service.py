"""Service layer for running the Atalanta ATPG oracle pipeline."""
import os
from typing import Any, Dict, Optional

from pyatpg.pipeline.phase1_structural import StructuralAnalyzer
from pyatpg.pipeline.phase2_ai import AIPrioritizer
from pyatpg.pipeline.phase3_oracle import OracleExecutor
from pyatpg.pipeline.phase4_validation import Validator


class AtpgService:
    """Stateless orchestration of the structural -> AI -> Atalanta -> validation pipeline."""

    @staticmethod
    def run(
        verilog_text: str,
        filename: str = "circuit.v",
        patterns: int = 3000,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        analyzer = StructuralAnalyzer()
        circuit, circuit_source, filename = analyzer.parse_circuit(
            source_text=verilog_text, filename=filename
        )

        clean_name = filename.replace('.v', '').replace('.bench', '')
        if clean_name.endswith('_mod'):
            clean_name = clean_name[:-4]

        phase1_data = analyzer.analyze(circuit, patterns=patterns)

        api_key = os.environ.get("GEMINI_API_KEY") if use_llm else None
        prioritizer = AIPrioritizer(api_key=api_key)
        suspicious_gates = prioritizer.prioritize(
            profiles=phase1_data['profiles'],
            summary=phase1_data['llm_summary'],
        )

        executor = OracleExecutor(output_dir="./atalanta_jobs/")
        extract_dir, _stdout = executor.execute(
            circuit=circuit,
            circuit_source=circuit_source,
            circuit_name=clean_name,
            flagged_gates=suspicious_gates,
        )

        targeted_log = os.path.join(extract_dir, "targeted_atalanta.log")
        baseline_log = os.path.join(extract_dir, "baseline_atalanta.log")
        fault_detail_log = os.path.join(extract_dir, f"{clean_name}.ufaults")

        validator = Validator()
        report = validator.validate(
            targeted_log=targeted_log,
            baseline_log=baseline_log,
            fault_detail_log=fault_detail_log,
            ai_suspects=suspicious_gates,
            profiles=phase1_data['profiles'],
            circuit_stats=phase1_data['statistics'],
        )
        report['circuit_name'] = clean_name
        report['ai_suspects'] = suspicious_gates
        report['used_llm'] = api_key is not None

        # With zero flagged gates, the targeted run has zero faults to target:
        # Atalanta reports "-nan%" coverage in that case, which our parser
        # reads as 0. That's not a real result, so don't present it as one.
        if not suspicious_gates:
            report['targeted_coverage'] = None
            report['comparison'] = None
            report['note'] = 'No gates were flagged as suspicious, so no targeted ATPG run was meaningful to perform.'

        return report
