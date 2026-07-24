import pytest
from pyatpg.evaluation import metrics
from pyatpg.ranking.models import RankingResult, RankedGate
from pyatpg.analysis.models import GroundTruth
from pyatpg.evaluation.evaluator import Evaluator

def test_metrics_top_k():
    ranked = ["G1", "G2", "G3", "G4", "G5"]
    trojan = {"G3", "G5"}
    
    # G3 is at index 2 (rank 3)
    assert metrics.top_k_accuracy(ranked, trojan, 1) == 0.0
    assert metrics.top_k_accuracy(ranked, trojan, 2) == 0.0
    assert metrics.top_k_accuracy(ranked, trojan, 3) == 1.0
    assert metrics.top_k_accuracy(ranked, trojan, 5) == 1.0

def test_metrics_recall_precision():
    ranked = ["G1", "G2", "G3", "G4", "G5"]
    trojan = {"G3", "G5"}
    
    assert metrics.recall_at_k(ranked, trojan, 3) == 0.5 # 1 out of 2 found
    assert metrics.recall_at_k(ranked, trojan, 5) == 1.0 # 2 out of 2 found
    
    assert metrics.precision_at_k(ranked, trojan, 3) == 1/3
    assert metrics.precision_at_k(ranked, trojan, 5) == 2/5

def test_metrics_mrr_and_ssr():
    ranked = ["G1", "G2", "G3", "G4", "G5"]
    trojan = {"G3"}
    
    assert metrics.mean_reciprocal_rank(ranked, trojan) == 1/3
    
    # rank is 3, total is 5
    # space skipped is 2. Reduction = 2/5 = 40%
    assert metrics.search_space_reduction(ranked, trojan, 5) == 40.0

def test_evaluator():
    ranked_gates = tuple(RankedGate(f"G{i}", 1.0, i) for i in range(1, 6))
    ranking = RankingResult("c17", ranked_gates, "MockRanker", 0.1)
    gt = GroundTruth(1, ["G2", "G3"], "G4", "net_out")
    
    # Trojan gates: G2, G3, G4
    # Ranked: G1, G2, G3, G4, G5
    # First trojan is G2 (rank 2)
    
    evaluator = Evaluator(k=3)
    res = evaluator.evaluate(ranking, gt)
    
    assert res.ranker_name == "MockRanker"
    assert res.top1_accuracy == 0.0
    assert res.top5_accuracy == 1.0
    assert res.mean_reciprocal_rank == 1/2
    assert res.recall_at_k == 2/3 # G2, G3 found in top 3
