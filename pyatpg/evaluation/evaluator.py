from pyatpg.analysis.models import GroundTruth
from pyatpg.ranking.models import RankingResult
from pyatpg.evaluation.models import EvaluationResult
from pyatpg.evaluation import metrics


class Evaluator:
    """Computes evaluation metrics for a RankingResult against GroundTruth.

    Agnostic to the ranking source. Consumes only canonical models.
    """

    def __init__(self, k: int = 5):
        self.k = k

    def evaluate(
        self,
        ranking: RankingResult,
        ground_truth: GroundTruth,
    ) -> EvaluationResult:
        """Evaluate a ranking against ground truth.

        Args:
            ranking: Output of any GateRanker.
            ground_truth: Known Trojan gate information.

        Returns:
            An immutable EvaluationResult.
        """
        ranked_ids = [g.gate_id for g in ranking.ranked_gates]
        total_gates = len(ranked_ids)

        # Build the set of known Trojan gate IDs.
        # A gate is considered a Trojan gate if it is:
        #  - listed in trigger_gates
        #  - the payload_gate
        #  - the affected_net (if it maps to a gate)
        trojan_ids: set[str] = set(ground_truth.trigger_gates)
        if ground_truth.payload_gate:
            trojan_ids.add(ground_truth.payload_gate)

        return EvaluationResult(
            ranker_name=ranking.ranker_name,
            circuit_name=ranking.circuit_name,
            top1_accuracy=metrics.top_k_accuracy(ranked_ids, trojan_ids, 1),
            top5_accuracy=metrics.top_k_accuracy(ranked_ids, trojan_ids, 5),
            top10_accuracy=metrics.top_k_accuracy(ranked_ids, trojan_ids, 10),
            recall_at_k=metrics.recall_at_k(ranked_ids, trojan_ids, self.k),
            precision_at_k=metrics.precision_at_k(ranked_ids, trojan_ids, self.k),
            mean_reciprocal_rank=metrics.mean_reciprocal_rank(ranked_ids, trojan_ids),
            search_space_reduction=metrics.search_space_reduction(
                ranked_ids, trojan_ids, total_gates
            ),
            k=self.k,
        )
