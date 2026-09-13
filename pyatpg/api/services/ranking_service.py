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
    def get_ranker(
        name: str,
        api_key: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
    ) -> GateRanker:
        """Instantiate a ranker by name.

        LLM-backed rankers receive the request's credential/provider so they
        can be supplied per call rather than only through the environment.
        """
        if name not in _RANKER_REGISTRY:
            raise ValueError(f"Unknown ranker: {name}")
        ranker_cls = _RANKER_REGISTRY[name]
        if ranker_cls is LLMRanker:
            from pyatpg.ranking.providers import provider_from_request

            provider = provider_from_request(
                provider=llm_provider, api_key=api_key, model=llm_model, base_url=llm_base_url
            )
            return LLMRanker(provider=provider)
        return ranker_cls()

    @staticmethod
    def rank(
        feature_set: FeatureSet,
        ranker_name: str,
        api_key: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
    ) -> RankingResult:
        ranker = RankingService.get_ranker(
            ranker_name,
            api_key=api_key,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
        )
        return ranker.rank(feature_set)
