"""A plain classifier on the same structural facts the LLM judge sees.

Context "D" shows the LLM :func:`~pyatpg.evaluation.trojan_candidates.candidate_facts`.
If the LLM then beats the rarity rule, the gain could come from the facts
rather than from the model's reasoning. This baseline separates the two: a
logistic regression on exactly those facts (plus the relaxed rule's score),
trained on the dev circuits only and frozen before any test run. The LLM
adds value only if it beats this, not just the rarity rule.

Pure Python on purpose: the data is a few thousand rows, and the project
does not otherwise depend on numpy or scikit-learn.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

#: Facts used, in order. Toggle-like values enter as log10 so that 0.001 vs
#: 0.01 counts as much as 0.01 vs 0.1.
FEATURES = (
    "relaxed_score",
    "log_trigger_toggle",
    "share_gates_as_rare",
    "trigger_fan_in",
    "trigger_siblings",
    "trigger_input_depth_spread",
    "via_inverter",
    "payload_type_share_circuit",
    "payload_type_share_nearby",
    "payload_fan_out",
    "log_payload_outputs_reached",
    "payload_levels_to_output",
    "log_other_toggle",
    "other_fan_out",
    "log_neighbourhood_median_toggle",
)


def feature_vector(facts: dict[str, float], relaxed_score: float) -> list[float]:
    def lg(v: float) -> float:
        return math.log10(float(v) + 1e-4)

    derived = {
        **facts,
        "relaxed_score": relaxed_score,
        "log_trigger_toggle": lg(facts["trigger_toggle"]),
        "log_other_toggle": lg(facts["other_toggle"]),
        "log_neighbourhood_median_toggle": lg(facts["neighbourhood_median_toggle"]),
        "log_payload_outputs_reached": math.log10(1 + facts["payload_outputs_reached"]),
    }
    return [float(derived[name]) for name in FEATURES]


def train(
    X: list[list[float]], y: list[int], l2: float = 1.0, iters: int = 2000, lr: float = 0.5
) -> dict[str, Any]:
    """Class-balanced, L2-regularised logistic regression by gradient descent.

    Features are standardised with the training set's mean/std, stored in
    the model so scoring applies the same transform. Identical rows (the
    same ordinary candidate recurs in every variant of a circuit) are merged
    and weighted by their count, which gives the same fit far faster.
    """
    n, d = len(X), len(X[0])
    counts: dict[tuple, int] = {}
    for r, t in zip(X, y):
        key = (tuple(r), t)
        counts[key] = counts.get(key, 0) + 1
    rows = [(list(k[0]), k[1], c) for k, c in counts.items()]
    mean = [sum(r[j] for r in X) / n for j in range(d)]
    std = [math.sqrt(sum((r[j] - mean[j]) ** 2 for r in X) / n) or 1.0 for j in range(d)]
    Z = [([(r[j] - mean[j]) / std[j] for j in range(d)], t, c) for r, t, c in rows]
    pos = sum(y)
    if pos == 0 or pos == n:
        raise ValueError("training data needs both trojan and ordinary candidates")
    # Balance the classes: one trojan per circuit against dozens of others.
    w_pos, w_neg = n / (2 * pos), n / (2 * (n - pos))
    w = [0.0] * d
    b = 0.0
    for _ in range(iters):
        gw = [0.0] * d
        gb = 0.0
        for z, t, c in Z:
            s = b + sum(wj * zj for wj, zj in zip(w, z))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, s))))
            g = c * (p - t) * (w_pos if t else w_neg)
            gb += g
            for j in range(d):
                gw[j] += g * z[j]
        b -= lr * gb / n
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j] / n)
    return {"features": list(FEATURES), "mean": mean, "std": std, "weights": w, "bias": b,
            "l2": l2, "n_train": n, "n_pos": pos}


def score(model: dict[str, Any], x: list[float]) -> float:
    """Trojan probability under a trained model."""
    if model["features"] != list(FEATURES):
        raise ValueError("model was trained on a different feature list")
    s = model["bias"] + sum(
        w * (v - m) / sd for w, v, m, sd in zip(model["weights"], x, model["mean"], model["std"]))
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, s))))


def load_models(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def model_for(models: dict[str, dict[str, Any]], circuit: str) -> dict[str, Any]:
    """The model a circuit may be scored with: never one trained on that circuit."""
    held_out = models.get(f"without:{circuit}")
    if held_out is not None:
        return held_out
    if circuit in models.get("all", {}).get("circuits", []):
        raise ValueError(f"no held-out model for training circuit {circuit}")
    return models["all"]
