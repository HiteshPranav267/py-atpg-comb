import pytest
import json
from pyatpg.core.netlist import Circuit
from pyatpg.analysis import (
    FeatureExtractor, ATPGResult, GroundTruth, FeatureSet, GateFeature, GateRole
)
from dataclasses import FrozenInstanceError

def test_feature_extractor_fusion():
    c = Circuit()
    c.name = "test_circuit"
    c.add_input("A")
    c.add_input("B")
    c.add_output("OUT1")
    c.add_gate("AND", "N1", ["A", "B"])
    c.add_gate("XOR", "OUT1", ["N1", "HT_TRIGGER_1"])
    c.add_gate("AND", "HT_TRIGGER_1", ["A"])
    
    atpg = ATPGResult(global_coverage=95.5, total_patterns=10, gate_statuses={
        "N1": {"detectable": True, "undetected": False, "fault_count": 2},
        "HT_TRIGGER_1": {"detectable": False, "undetected": True, "fault_count": 2}
    })
    
    gt = GroundTruth(trojan_id=1, trigger_gates=["HT_TRIGGER_1"], payload_gate="OUT1", affected_net="OUT1")
    
    extractor = FeatureExtractor()
    features = extractor.extract(c, atpg, gt)
    
    assert features.circuit_name == "test_circuit"
    assert features.atpg_coverage == 95.5
    
    # Check extraction of specific gates
    n1_feat = next(f for f in features.gate_features if f.gate_id == "N1")
    assert n1_feat.role == GateRole.INTERNAL
    assert n1_feat.detectable == True
    assert n1_feat.is_trojan_gate == False
    
    ht_feat = next(f for f in features.gate_features if f.gate_id == "HT_TRIGGER_1")
    assert ht_feat.is_trigger_gate == True
    assert ht_feat.undetected == True
    assert ht_feat.detectable == False
    
def test_gate_feature_immutability():
    feat = GateFeature(
        gate_id="N1", gate_type="AND", role=GateRole.INTERNAL,
        level=1, fanin=2, fanout=1, pi_distance=1, po_distance=2,
        cc0=2, cc1=2, co=3, toggle_rate=0.25
    )
    
    with pytest.raises(FrozenInstanceError):
        feat.level = 5

def test_serialization():
    feat = GateFeature(
        gate_id="N1", gate_type="AND", role=GateRole.INTERNAL,
        level=1, fanin=2, fanout=1, pi_distance=1, po_distance=2,
        cc0=2, cc1=2, co=3, toggle_rate=0.25, is_trigger_gate=True
    )
    
    fset = FeatureSet(circuit_name="c17", gate_features=[feat], atpg_coverage=99.9)
    
    # Serialize
    json_str = fset.to_json()
    
    # Deserialize
    fset_restored = FeatureSet.from_json(json_str)
    
    assert fset_restored.circuit_name == "c17"
    assert fset_restored.atpg_coverage == 99.9
    assert len(fset_restored.gate_features) == 1
    
    feat_restored = fset_restored.gate_features[0]
    
    # Assert objects equal
    assert feat == feat_restored
    assert feat_restored.is_trigger_gate == True
    assert feat_restored.role == GateRole.INTERNAL
