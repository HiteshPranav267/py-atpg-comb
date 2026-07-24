import json
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class EvaluationResult:
    """Canonical output of every evaluation run.

    All accuracy and recall/precision values are floats in [0.0, 1.0].
    search_space_reduction is expressed as a percentage reduction.
    """
    ranker_name: str
    circuit_name: str
    top1_accuracy: float
    top5_accuracy: float
    top10_accuracy: float
    recall_at_k: float
    precision_at_k: float
    mean_reciprocal_rank: float
    search_space_reduction: float
    k: int = 5  # the k used for recall/precision

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'EvaluationResult':
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> 'EvaluationResult':
        return cls.from_dict(json.loads(json_str))
