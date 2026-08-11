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
    def get_ranker(name: str, api_key: str | None = None) -> GateRanker:
        """Instantiate a ranker by name.

        LLM-backed rankers receive the request's credential so a key can be
        supplied per call rather than only through the environment.
        """
        if name not in _RANKER_REGISTRY:
            raise ValueError(f"Unknown ranker: {name}")
        ranker_cls = _RANKER_REGISTRY[name]
        if ranker_cls is LLMRanker and api_key:
            from pyatpg.ranking.providers import GeminiProvider

            return LLMRanker(provider=GeminiProvider(api_key=api_key))
        return ranker_cls()

    @staticmethod
    def rank(
        feature_set: FeatureSet, ranker_name: str, api_key: str | None = None
    ) -> RankingResult:
        ranker = RankingService.get_ranker(ranker_name, api_key=api_key)
        return ranker.rank(feature_set)
