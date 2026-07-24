import time
from typing import Dict, Any, Type
from pyatpg.ranking import GateRanker, RandomRanker, ScoapRanker, RareNodeRanker, LLMRanker
from pyatpg.ranking.models import RankingResult
from pyatpg.analysis.models import FeatureSet

# Registry of available rankers
_RANKER_REGISTRY: Dict[str, Type[GateRanker]] = {
    "RandomRanker": RandomRanker,
    "ScoapRanker": ScoapRanker,
    "RareNodeRanker": RareNodeRanker,
    "LLMRanker": LLMRanker,
}

class RankingService:
    @staticmethod
    def get_available_rankers() -> list[str]:
        return list(_RANKER_REGISTRY.keys())
        
    @staticmethod
    def get_ranker(name: str) -> GateRanker:
        if name not in _RANKER_REGISTRY:
            raise ValueError(f"Unknown ranker: {name}")
        return _RANKER_REGISTRY[name]()
        
    @staticmethod
    def rank(feature_set: FeatureSet, ranker_name: str) -> RankingResult:
        ranker = RankingService.get_ranker(ranker_name)
        return ranker.rank(feature_set)
