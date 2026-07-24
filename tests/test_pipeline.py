import os
import pytest
from pyatpg.pipeline.phase1_structural import StructuralAnalyzer
from pyatpg.pipeline.phase2_ai import AIPrioritizer
from pyatpg.pipeline.phase3_oracle import OracleExecutor
from pyatpg.pipeline.phase4_validation import Validator
from pyatpg.pipeline import PyATPGPipeline

def test_structural_analyzer():
    analyzer = StructuralAnalyzer()
    # Assuming c17.v is present for a sanity check
    circuit, src, name = analyzer.parse_circuit(filepath="benchmarks/c17.v")
    assert circuit is not None
    assert name == "c17.v"
    
    res = analyzer.analyze(circuit, patterns=10)
    assert 'statistics' in res
    assert 'profiles' in res

def test_ai_prioritizer():
    prioritizer = AIPrioritizer()
    # Test offline heuristic
    mock_profiles = [
        {'gate_id': 'G1', 'features': {'toggle_rate': 0.05, 'fan_in': 2, 'co': 10}}, # Suspicious
        {'gate_id': 'G2', 'features': {'toggle_rate': 0.8, 'fan_in': 1, 'co': 1}}    # Normal
    ]
    res = prioritizer.prioritize(mock_profiles)
    assert 'G1' in res
    assert 'G2' not in res

def test_validator():
    validator = Validator()
    mock_details = [
        {'fault_name': 'G1', 'status': 'undetected'},
        {'fault_name': 'G2', 'status': 'detected'}
    ]
    trojans, cleared, unknown = validator.cross_reference(['G1', 'G2', 'G3'], mock_details)
    
    assert len(trojans) == 1
    assert trojans[0]['gate_id'] == 'G1'
    
    assert len(cleared) == 1
    assert cleared[0]['gate_id'] == 'G2'
    
    assert len(unknown) == 1
    assert unknown[0]['gate_id'] == 'G3'
