from abc import ABC, abstractmethod
from pyatpg.analysis.models import FeatureSet
from pyatpg.ranking.models import RankingResult


class GateRanker(ABC):
    """Abstract base class for all gate ranking algorithms.

    Every ranking algorithm — heuristic, LLM-based, or ML-based — MUST
    inherit from this interface and implement the `rank` method.

    The ranker receives a FeatureSet (the canonical circuit representation)
    and returns a RankingResult (the canonical ranking representation).

    No ranker should ever access Circuit, Parser, Graph, ATPG logs,
    or raw metadata directly.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this ranker (used in reports)."""
        ...

    @abstractmethod
    def rank(self, feature_set: FeatureSet) -> RankingResult:
        """Rank all gates in the circuit by suspicion score.

        Args:
            feature_set: The canonical FeatureSet for a circuit.

        Returns:
            A RankingResult containing all gates ordered by suspicion.
        """
        ...
