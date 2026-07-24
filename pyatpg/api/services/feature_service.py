"""Service layer for feature extraction."""
from pyatpg.analysis.feature_extractor import FeatureExtractor
from pyatpg.analysis.adapters import AtalantaAdapter, MetadataAdapter
from pyatpg.analysis.models import FeatureSet, ATPGResult, GroundTruth
from pyatpg.api.services.circuit_service import CircuitService
from typing import Optional


class FeatureService:
    """Stateless service for extracting features from a circuit."""

    @staticmethod
    def extract_features(
        verilog_text: str,
        circuit_name: str = "unknown",
        metadata_path: Optional[str] = None,
        atpg_log_path: Optional[str] = None,
    ) -> FeatureSet:
        circuit = CircuitService.parse_to_circuit(verilog_text, circuit_name)
        extractor = FeatureExtractor()

        ground_truth = None
        if metadata_path:
            ground_truth = MetadataAdapter.parse_json(metadata_path)

        atpg_result = None
        if atpg_log_path:
            atpg_result = AtalantaAdapter.parse_report(atpg_log_path)

        return extractor.extract(
            circuit=circuit,
            atpg_result=atpg_result,
            ground_truth=ground_truth,
        )
