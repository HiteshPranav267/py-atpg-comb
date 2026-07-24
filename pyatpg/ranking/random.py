import time
import random as _random
from pyatpg.analysis.models import FeatureSet
from pyatpg.ranking.base import GateRanker
from pyatpg.ranking.models import RankingResult, RankedGate


class RandomRanker(GateRanker):
    """Lower-bound baseline. Ranks all gates in random order.

    Useful for establishing the absolute floor performance that any
    meaningful ranking algorithm must exceed.
    """

    def __init__(self, seed: int | None = None):
        self._seed = seed

    @property
    def name(self) -> str:
        return "RandomRanker"

    def rank(self, feature_set: FeatureSet) -> RankingResult:
        start = time.time()

        gates = list(feature_set.gate_features)
        rng = _random.Random(self._seed)
        rng.shuffle(gates)

        ranked = tuple(
            RankedGate(
                gate_id=g.gate_id,
                score=round(1.0 - (i / max(len(gates), 1)), 6),
                rank=i + 1,
                reason="random",
            )
            for i, g in enumerate(gates)
        )

        return RankingResult(
            circuit_name=feature_set.circuit_name,
            ranked_gates=ranked,
            ranker_name=self.name,
            execution_time=time.time() - start,
        )
