"""LLM-2: test-target prioritization over a small, retrieval-built context.

Matches the ``CandidateRanker`` shape :mod:`pyatpg.seq.engine` expects, so
it plugs in as ``SequentialAtpgEngine(..., candidate_ranker=prioritizer.rank)``
with the engine never importing anything from this package.

Only ever sends the top-k *currently hardest* faults (by local SCOAP
difficulty), their immediate graph neighborhood, and history entries that
touched those same nodes -- see ``LocalContextStore.compact_fault_context``.
The full undetected-fault list, the full circuit, and the full simulation
history stay local. The model's response is validated against the batch it
was given (unknown fault names are dropped) before being trusted, the same
grounding pattern already used for combinational gate targeting.
"""
from __future__ import annotations

from typing import Any

from pyatpg.seq.models import SequentialFault

from .store import LocalContextStore
from .token_tracker import TokenTracker

try:
    from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError
except ImportError:  # pragma: no cover
    LLMProvider = None  # type: ignore[assignment,misc]

    class LLMUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Placeholder so the except clause below still resolves."""


class LLMFaultPrioritizer:
    def __init__(
        self,
        provider: "LLMProvider | None",
        store: LocalContextStore,
        tracker: TokenTracker,
        circuit_summary: str,
        max_faults_per_call: int = 6,
    ):
        self.provider = provider
        self.store = store
        self.tracker = tracker
        self.circuit_summary = circuit_summary
        self.max_faults_per_call = max_faults_per_call

    def rank(self, faults: list[SequentialFault], extra: dict[str, Any]) -> list[SequentialFault] | None:
        if self.provider is None or not faults:
            return None

        batch = self.store.top_k_difficult(faults, self.max_faults_per_call)
        batch_names = {f.name: f for f in batch}
        context = self.store.compact_fault_context(batch, max_faults=self.max_faults_per_call)

        prompt = (
            f"{self.circuit_summary}\n\n"
            "You are prioritizing which stuck-at faults to target next in a sequential "
            "ATPG search. A deterministic simulator -- not you -- decides what is "
            "actually detected; you only choose an attempt order.\n\n"
            f"Candidate faults (compact context, not the whole circuit):\n{context}\n\n"
            "Return only the fault names above, comma-separated, ordered most-worth-"
            "attempting first. Do not invent fault names."
        )
        try:
            response = self.provider.complete(prompt)
        except LLMUnavailableError:
            # Provider could not be reached at all: abort the run rather
            # than silently continuing in natural fault order.
            raise
        except Exception:  # noqa: BLE001 - model ran but its answer was unusable
            return None
        self.tracker.record("llm2", prompt, response)

        parsed = [tok.strip() for tok in response.replace("\n", ",").split(",") if tok.strip()]
        ordered_batch = [batch_names[name] for name in parsed if name in batch_names]
        for f in batch:
            if f not in ordered_batch:
                ordered_batch.append(f)

        rest = [f for f in faults if f not in batch_names.values()]
        return ordered_batch + rest
