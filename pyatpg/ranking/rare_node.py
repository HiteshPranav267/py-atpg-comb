import time
from pyatpg.analysis.models import FeatureSet, GateRole
from pyatpg.ranking.base import GateRanker
from pyatpg.ranking.models import RankingResult, RankedGate


class RareNodeRanker(GateRanker):
    """Ranks gates by toggle-rate rarity and structural isolation.

    Intuition: Hardware Trojans are designed to remain dormant under
    normal operation. Gates with low toggle rates (rarely switching)
    and high fan-in (many conditions required to activate) are prime
    candidates for Trojan trigger logic.

    Reads only from FeatureSet. No additional graph analysis.
    """

    @property
    def name(self) -> str:
        return "RareNodeRanker"

    def rank(self, feature_set: FeatureSet) -> RankingResult:
        start = time.time()

        scored: list[tuple[str, float, str]] = []

        for gf in feature_set.gate_features:
            if gf.role == GateRole.INPUT:
                continue

            # Toggle rarity: lower toggle → higher suspicion
            # Invert so that low toggle gives high score
            toggle_rarity = 1.0 - min(gf.toggle_rate, 1.0)

            # Structural rarity: high fan-in gates with low fan-out
            # are structurally characteristic of trigger logic
            structural_rarity = gf.fanin / (gf.fanout + 1.0)

            # Depth factor: gates deep in the logic cone are harder
            # to activate from primary inputs
            depth_factor = gf.pi_distance / max(gf.pi_distance + gf.po_distance, 1)

            suspicion = (
                toggle_rarity * 0.50
                + structural_rarity * 0.25
                + depth_factor * 0.25
            )

            reason = (
                f"toggle={gf.toggle_rate:.4f} "
                f"fanin={gf.fanin} fanout={gf.fanout} "
                f"depth={gf.pi_distance}"
            )
            scored.append((gf.gate_id, suspicion, reason))

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
