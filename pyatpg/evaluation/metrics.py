"""Independently testable metric functions for Hardware Trojan localization.

Every function takes the ranked gate IDs and the set of ground-truth
Trojan gate IDs, and returns a scalar float.
"""

from typing import List, Set


def top_k_accuracy(ranked_gate_ids: List[str], trojan_gate_ids: Set[str], k: int) -> float:
    """Returns 1.0 if ANY Trojan gate appears in the top-k, else 0.0."""
    if not trojan_gate_ids:
        return 0.0
    top_k = set(ranked_gate_ids[:k])
    return 1.0 if top_k & trojan_gate_ids else 0.0


def recall_at_k(ranked_gate_ids: List[str], trojan_gate_ids: Set[str], k: int) -> float:
    """Fraction of Trojan gates found in the top-k.

    recall@k = |trojan ∩ top_k| / |trojan|
    """
    if not trojan_gate_ids:
        return 0.0
    top_k = set(ranked_gate_ids[:k])
    found = top_k & trojan_gate_ids
    return len(found) / len(trojan_gate_ids)


def precision_at_k(ranked_gate_ids: List[str], trojan_gate_ids: Set[str], k: int) -> float:
    """Fraction of top-k gates that are actual Trojans.

    precision@k = |trojan ∩ top_k| / k
    """
    if k == 0:
        return 0.0
    top_k = set(ranked_gate_ids[:k])
    found = top_k & trojan_gate_ids
    return len(found) / k


def mean_reciprocal_rank(ranked_gate_ids: List[str], trojan_gate_ids: Set[str]) -> float:
    """1 / (position of the first Trojan gate in the ranking).

    MRR = 0.0 if no Trojan gate appears in the ranking.
    """
    if not trojan_gate_ids:
        return 0.0
    for i, gate_id in enumerate(ranked_gate_ids):
        if gate_id in trojan_gate_ids:
            return 1.0 / (i + 1)
    return 0.0


def search_space_reduction(
    ranked_gate_ids: List[str],
    trojan_gate_ids: Set[str],
    total_gates: int,
) -> float:
    """Percentage of gates that can be skipped before the first Trojan is found.

    If the first Trojan appears at rank r out of N total gates,
    reduction = (N - r) / N × 100.

    Higher is better — it means the ranker pushed Trojans toward the top.
    """
    if total_gates == 0 or not trojan_gate_ids:
        return 0.0
    for i, gate_id in enumerate(ranked_gate_ids):
        if gate_id in trojan_gate_ids:
            return ((total_gates - (i + 1)) / total_gates) * 100.0
    return 0.0
