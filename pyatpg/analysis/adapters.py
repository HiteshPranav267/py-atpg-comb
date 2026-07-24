import json
import os
from typing import Dict, Any
from .models import ATPGResult, GroundTruth

class AtalantaAdapter:
    @staticmethod
    def parse_report(report_path: str) -> ATPGResult:
        """
        Parses an Atalanta .txt report.
        For now, this serves as a stub returning mock data,
        but it illustrates the decoupling principle.
        """
        # In a real implementation, you would parse the Atalanta .rep file to extract:
        # 1. Total coverage
        # 2. Total patterns
        # 3. Per-fault status mapping
        return ATPGResult(
            global_coverage=0.0,
            total_patterns=0,
            gate_statuses={}
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
