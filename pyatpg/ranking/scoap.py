import time
from pyatpg.analysis.models import FeatureSet, GateRole
from pyatpg.ranking.base import GateRanker
from pyatpg.ranking.models import RankingResult, RankedGate


class ScoapRanker(GateRanker):
    """Ranks gates by SCOAP-derived suspicion heuristic.

    Intuition: A gate that is hard to control (high CC0 + CC1) and hard
    to observe (high CO) is inherently difficult to test. Hardware Trojans
    exploit exactly these blind spots. Gates with the highest combined
    testability difficulty are ranked as most suspicious.

    Reads only from FeatureSet. Does NOT recompute SCOAP.
    """

    @property
    def name(self) -> str:
        return "ScoapRanker"

    def rank(self, feature_set: FeatureSet) -> RankingResult:
        start = time.time()

        scored: list[tuple[str, float, str]] = []

        for gf in feature_set.gate_features:
            # Skip primary inputs — they cannot be Trojan gates
            if gf.role == GateRole.INPUT:
                continue

            # Suspicion = combined controllability difficulty + observability difficulty
            # Higher values = harder to test = more suspicious
            suspicion = (gf.cc0 + gf.cc1) * 0.4 + gf.co * 0.6

            reason = f"CC0={gf.cc0} CC1={gf.cc1} CO={gf.co}"
            scored.append((gf.gate_id, suspicion, reason))

        # Sort descending by suspicion
        scored.sort(key=lambda x: x[1], reverse=True)

        ranked = tuple(
            RankedGate(
                gate_id=gate_id,
                score=round(score, 6),
                rank=i + 1,
                reason=reason,
            )
            for i, (gate_id, score, reason) in enumerate(scored)
        )

        return RankingResult(
            circuit_name=feature_set.circuit_name,
            ranked_gates=ranked,
            ranker_name=self.name,
            execution_time=time.time() - start,
        )
