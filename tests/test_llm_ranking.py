import pytest
import json
from pyatpg.analysis.models import FeatureSet, GateFeature, GateRole
from pyatpg.ranking.prompt_builder import PromptBuilder
from pyatpg.ranking.response_parser import ResponseParser, MalformedRankingError
from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError
from pyatpg.ranking.llm import LLMRanker

#: Groq's wording when one request exceeds its free-tier per-minute token
#: budget. Verbatim (bar the org id) because the fallback keys off the
#: provider's own phrasing, not a status code.
GROQ_TOO_LARGE = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 8000, Requested 8515, please reduce your "
    "message size and try again. Need more tokens? Upgrade to Dev Tier today at "
    "https://console.groq.com/settings/billing', 'type': 'tokens', 'code': "
    "'rate_limit_exceeded'}}"
)

@pytest.fixture
def sample_feature_set():
    gates = [
        GateFeature("IN1", "INPUT", GateRole.INPUT, 0, 0, 2, 0, 3, 0, 0, 0, 0.5),
        GateFeature("G1", "AND", GateRole.INTERNAL, 1, 2, 1, 1, 2, 2, 3, 4, 0.2, undetected=True),
        GateFeature("G2", "OR", GateRole.INTERNAL, 2, 2, 1, 2, 1, 5, 5, 10, 0.1)
    ]
    return FeatureSet("test_circuit", gates)

class MockProvider(LLMProvider):
    def complete(self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None) -> str:
        # Returns a valid markdown-wrapped JSON response
        return '''```json
{
    "ranking": [
        {
            "gate": "G2",
            "confidence": 0.95,
            "reason": "Very suspicious."
        },
        {
            "gate": "G1",
            "confidence": 0.8,
            "reason": "Somewhat suspicious."
        }
    ]
}
```'''

def test_prompt_builder(sample_feature_set):
    builder = PromptBuilder()
    prompt = builder.build(sample_feature_set)
    
    assert "You are an expert hardware security engineer" in prompt
    assert "IN1" not in prompt # Input should be filtered out
    assert "G1" in prompt
    assert "G2" in prompt
    assert '"gate_id": "G1"' in prompt

def test_response_parser_valid():
    parser = ResponseParser()
    raw = '```json\n{"ranking": [{"gate": "G1", "confidence": 0.9}]}\n```'
    parsed = parser.parse(raw)
    assert len(parsed) == 1
    assert parsed[0]["gate"] == "G1"
    assert parsed[0]["confidence"] == 0.9

def test_response_parser_invalid():
    parser = ResponseParser()
    
    with pytest.raises(MalformedRankingError, match="JSON root must be an object"):
        parser.parse('["not", "an", "object"]')
        
    with pytest.raises(MalformedRankingError, match="JSON must contain a 'ranking' key"):
        parser.parse('{"foo": "bar"}')
        
    with pytest.raises(MalformedRankingError, match="Each ranking item must have a 'gate' key"):
        parser.parse('{"ranking": [{"confidence": 0.9}]}')

class _RejectsVerbosePrompt(LLMProvider):
    """Accepts the compact prompt, rejects the labelled-JSON one.

    Stands in for a small-context model, or for Groq's free tier, which
    counts a request against a per-minute token budget and refuses it
    outright when prompt-plus-reservation crosses the line.
    """

    def __init__(self, ranked_gate="G1"):
        self.templates_tried = []
        self._ranked_gate = ranked_gate

    def complete(self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None) -> str:
        is_compact = "id,type,fanin" in prompt
        self.templates_tried.append("compact" if is_compact else "zero_shot")
        if not is_compact:
            raise LLMUnavailableError("Custom (OpenAI-compatible)", GROQ_TOO_LARGE)
        return json.dumps(
            {"ranking": [{"gate": self._ranked_gate, "confidence": 0.7, "reason": "rare"}]}
        )


def test_ranker_falls_back_to_the_compact_prompt_when_the_request_is_too_large(
    sample_feature_set,
):
    """A too-large request is the one failure worth resending smaller.

    Everything else about LLMUnavailableError stays fatal -- see the test
    below -- but here a terser prompt carrying the same gates is a real
    answer, and strictly better than aborting the run.
    """
    provider = _RejectsVerbosePrompt()
    result = LLMRanker(provider=provider).rank(sample_feature_set)

    assert provider.templates_tried == ["zero_shot", "compact"]
    assert len(result.ranked_gates) == 1
    # The result must own up to the template it actually used.
    assert result.ranker_name.endswith("_compact")


def test_ranker_does_not_fall_back_for_failures_a_smaller_prompt_cannot_fix(
    sample_feature_set,
):
    class _BadKey(LLMProvider):
        def __init__(self):
            self.calls = 0

        def complete(self, prompt: str, *, json_mode: bool = False, max_output_tokens: int | None = None) -> str:
            self.calls += 1
            raise LLMUnavailableError("X", "Error code: 401 - invalid api key")

    provider = _BadKey()
    with pytest.raises(LLMUnavailableError, match="authentication failed"):
        LLMRanker(provider=provider).rank(sample_feature_set)
    assert provider.calls == 1


def test_compact_prompt_is_materially_smaller_for_the_same_gates(sample_feature_set):
    builder = PromptBuilder()
    verbose = builder.build(sample_feature_set, template="zero_shot")
    compact = builder.build(sample_feature_set, template="compact")

    assert len(compact) < len(verbose)
    # Same gates, same schema asked for -- only the encoding changed.
    for gate_id in ("G1", "G2"):
        assert gate_id in compact
    assert "IN1" not in compact
    assert '"ranking"' in compact


def test_llm_ranker(sample_feature_set):
    # Pass our MockProvider so we don't make real API calls
    ranker = LLMRanker(provider=MockProvider())
    result = ranker.rank(sample_feature_set)
    
    assert result.circuit_name == "test_circuit"
    assert result.ranker_name == "LLMRanker_MockProvider_zero_shot"
    assert len(result.ranked_gates) == 2
    
    assert result.ranked_gates[0].gate_id == "G2"
    assert result.ranked_gates[0].score == 0.95
    assert result.ranked_gates[0].rank == 1
    
    assert result.ranked_gates[1].gate_id == "G1"
    assert result.ranked_gates[1].score == 0.8
    assert result.ranked_gates[1].rank == 2
