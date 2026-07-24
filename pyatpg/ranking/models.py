import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass(frozen=True)
class RankedGate:
    """A single gate with its ranking score and position."""
    gate_id: str
    score: float
    rank: int
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'RankedGate':
        return cls(**data)


@dataclass(frozen=True)
class RankingResult:
    """Canonical output of every GateRanker."""
    circuit_name: str
    ranked_gates: tuple  # tuple of RankedGate for immutability
    ranker_name: str
    execution_time: float

    def to_dict(self) -> dict:
        return {
            "circuit_name": self.circuit_name,
            "ranker_name": self.ranker_name,
            "execution_time": self.execution_time,
            "ranked_gates": [g.to_dict() for g in self.ranked_gates],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RankingResult':
        gates = tuple(RankedGate.from_dict(g) for g in data.get("ranked_gates", []))
        return cls(
            circuit_name=data["circuit_name"],
            ranked_gates=gates,
            ranker_name=data["ranker_name"],
            execution_time=data["execution_time"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> 'RankingResult':
        return cls.from_dict(json.loads(json_str))

    def top_k(self, k: int) -> list:
        """Return the top-k gate IDs."""
        return [g.gate_id for g in self.ranked_gates[:k]]
