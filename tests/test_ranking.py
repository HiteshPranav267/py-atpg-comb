import pytest
from pyatpg.analysis.models import FeatureSet, GateFeature, GateRole
from pyatpg.ranking.random import RandomRanker
from pyatpg.ranking.scoap import ScoapRanker
from pyatpg.ranking.rare_node import RareNodeRanker
from pyatpg.ranking.models import RankingResult

@pytest.fixture
def sample_feature_set():
    gates = [
        GateFeature("IN1", "INPUT", GateRole.INPUT, 0, 0, 2, 0, 3, 0, 0, 0, 0.5),
        GateFeature("G1", "AND", GateRole.INTERNAL, 1, 2, 1, 1, 2, 2, 3, 4, 0.2), # SCOAP: CC0=2, CC1=3, CO=4
        GateFeature("G2", "OR", GateRole.INTERNAL, 2, 2, 1, 2, 1, 5, 5, 10, 0.1), # High SCOAP, low toggle
        GateFeature("OUT1", "OUTPUT", GateRole.OUTPUT, 3, 1, 0, 3, 0, 1, 1, 0, 0.4)
    ]
    return FeatureSet("test_circuit", gates)

def test_random_ranker_determinism(sample_feature_set):
    r1 = RandomRanker(seed=42).rank(sample_feature_set)
    r2 = RandomRanker(seed=42).rank(sample_feature_set)
    assert [g.gate_id for g in r1.ranked_gates] == [g.gate_id for g in r2.ranked_gates]
    
def test_scoap_ranker(sample_feature_set):
    r = ScoapRanker().rank(sample_feature_set)
    ranked_ids = [g.gate_id for g in r.ranked_gates]
    assert "IN1" not in ranked_ids # inputs excluded
    
    # G2 CC0=5, CC1=5, CO=10 -> susp = 10*0.4 + 10*0.6 = 10.0
    # G1 CC0=2, CC1=3, CO=4 -> susp = 5*0.4 + 4*0.6 = 4.4
    # OUT1 CC0=1, CC1=1, CO=0 -> susp = 2*0.4 = 0.8
    assert ranked_ids[0] == "G2"
    assert ranked_ids[1] == "G1"
    assert ranked_ids[2] == "OUT1"
    
def test_rare_node_ranker(sample_feature_set):
    r = RareNodeRanker().rank(sample_feature_set)
    ranked_ids = [g.gate_id for g in r.ranked_gates]
    assert "IN1" not in ranked_ids
    # G2 has lowest toggle (0.1) and highest depth (2/3), so it should rank highest
    assert ranked_ids[0] == "G2"
