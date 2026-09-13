import time
from pyatpg.analysis.models import FeatureSet
from pyatpg.ranking.base import GateRanker
from pyatpg.ranking.models import RankingResult, RankedGate
from pyatpg.ranking.prompt_builder import PromptBuilder
from pyatpg.ranking.response_parser import ResponseParser
from pyatpg.ranking.providers import LLMProvider, provider_from_request


class LLMRanker(GateRanker):
    """Ranks gates using a Large Language Model.
    
    Orchestrates the PromptBuilder, LLMProvider, and ResponseParser
    while remaining fully compliant with the GateRanker interface.
    """

    def __init__(self, provider: LLMProvider | None = None, template: str = "zero_shot"):
        if provider is None:
            # No explicit provider: whichever the environment is configured
            # for (LLM_PROVIDER / *_API_KEY). Fail here, not at rank time.
            provider = provider_from_request()
            if provider is None:
                raise ValueError(
                    "No LLM API key available: supply one with the request or set "
                    "LLM_API_KEY (or a provider-specific variable such as "
                    "GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY)."
                )
        self.provider = provider
        self.template = template
        self.builder = PromptBuilder()
        self.parser = ResponseParser()

    @property
    def name(self) -> str:
        provider_name = self.provider.__class__.__name__
        return f"LLMRanker_{provider_name}_{self.template}"

    def rank(self, feature_set: FeatureSet) -> RankingResult:
        start = time.time()

        # 1. Build prompt
        prompt = self.builder.build(feature_set, template=self.template)

        # 2. Call LLM
        raw_response = self.provider.complete(prompt)

        # 3. Parse JSON
        parsed_ranking = self.parser.parse(raw_response)

        # 4. Convert to canonical format
        ranked_gates = []
        for i, item in enumerate(parsed_ranking):
            ranked_gates.append(
                RankedGate(
                    gate_id=item["gate"],
                    score=float(item.get("confidence", 0.0)),
                    rank=i + 1,
                    reason=item.get("reason")
                )
            )

        return RankingResult(
            circuit_name=feature_set.circuit_name,
            ranked_gates=tuple(ranked_gates),
            ranker_name=self.name,
            execution_time=time.time() - start
        )
