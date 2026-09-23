import time
from pyatpg.analysis.models import FeatureSet
from pyatpg.ranking.base import GateRanker
from pyatpg.ranking.models import RankingResult, RankedGate
from pyatpg.ranking.prompt_builder import TEMPLATE_FALLBACK_ORDER, PromptBuilder
from pyatpg.ranking.response_parser import ResponseParser
from pyatpg.ranking.providers import (
    LLMProvider,
    LLMUnavailableError,
    is_request_too_large,
    provider_from_request,
)


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

    def _name_for(self, template: str) -> str:
        """Ranker name reporting the template a run actually used.

        A run that fell back to a terser prompt says so rather than
        claiming the one it was configured with.
        """
        return f"LLMRanker_{self.provider.__class__.__name__}_{template}"

    def rank(self, feature_set: FeatureSet) -> RankingResult:
        start = time.time()

        # 1-2. Build the prompt and call the model, stepping down to a
        # terser template if the provider says the request was too big --
        # a small-context model, or a stingy free-tier per-minute budget,
        # then still gets a usable ranking instead of failing the run. Only
        # that one failure is caught: everything else still aborts, since
        # the point of LLMUnavailableError is not to quietly degrade.
        start_at = TEMPLATE_FALLBACK_ORDER.index(self.template)
        templates = TEMPLATE_FALLBACK_ORDER[start_at:]
        for attempt, template in enumerate(templates):
            prompt = self.builder.build(feature_set, template=template)
            try:
                raw_response = self.provider.complete(
                    prompt, max_output_tokens=self.builder.output_budget()
                )
                break
            except LLMUnavailableError as e:
                if attempt == len(templates) - 1 or not is_request_too_large(e.cause):
                    raise
        used_template = template

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
            ranker_name=self._name_for(used_template),
            execution_time=time.time() - start
        )
