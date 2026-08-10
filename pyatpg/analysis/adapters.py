import json
import os
from pathlib import Path
from typing import Dict, Any
from .models import ATPGResult, GroundTruth

class AtalantaAdapter:
    """Adapts a real Atalanta log into the evaluation layer's ATPGResult."""

    @staticmethod
    def parse_report(report_path: str) -> ATPGResult:
        """Read an Atalanta stdout log written by a real run.

        Delegates to the same parsers the ATPG pipeline uses, so there is one
        implementation of "what Atalanta said" rather than two that can drift.

        Raises:
            FileNotFoundError: if the log does not exist. Previously this
                returned zeroed results, which is indistinguishable from a
                genuine 0% coverage run.
        """
        from pyatpg.atpg.parsing import parse_summary_report

        path = Path(report_path)
        if not path.is_file():
            raise FileNotFoundError(f"Atalanta log not found: {report_path}")

        _, _, results, _, _ = parse_summary_report(path.read_text(encoding="utf-8", errors="replace"))
        return ATPGResult(
            global_coverage=results.fault_coverage,
            total_patterns=results.patterns_after_compaction,
            gate_statuses={},
        )

class MetadataAdapter:
    @staticmethod
    def parse_json(json_path: str) -> GroundTruth:
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"Metadata file not found: {json_path}")
            
        with open(json_path, 'r') as f:
            data = json.load(f)
            
        return GroundTruth(
            trojan_id=data.get('trojan_id', 0),
            trigger_gates=data.get('inserted_gates', []), # Using inserted gates for simplicity
            payload_gate=data.get('payload_target', ''),
            affected_net=data.get('affected_net', '')
        )
