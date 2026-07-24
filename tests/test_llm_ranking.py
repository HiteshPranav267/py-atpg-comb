import pytest
import json
from pyatpg.analysis.models import FeatureSet, GateFeature, GateRole
from pyatpg.ranking.prompt_builder import PromptBuilder
from pyatpg.ranking.response_parser import ResponseParser, MalformedRankingError
from pyatpg.ranking.providers import LLMProvider
from pyatpg.ranking.llm import LLMRanker

@pytest.fixture
def sample_feature_set():
    gates = [
        GateFeature("IN1", "INPUT", GateRole.INPUT, 0, 0, 2, 0, 3, 0, 0, 0, 0.5),
        GateFeature("G1", "AND", GateRole.INTERNAL, 1, 2, 1, 1, 2, 2, 3, 4, 0.2, undetected=True),
        GateFeature("G2", "OR", GateRole.INTERNAL, 2, 2, 1, 2, 1, 5, 5, 10, 0.1)
    ]
    return FeatureSet("test_circuit", gates)

class MockProvider(LLMProvider):
    def complete(self, prompt: str) -> str:
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
