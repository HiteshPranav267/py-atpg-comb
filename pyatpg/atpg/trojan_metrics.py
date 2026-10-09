"""Trojan-specific measurements that fault coverage cannot provide.

Stuck-at coverage is blind to a Trojan: on Trust-Hub s15850-T100 the clean
and infected circuits reach the same coverage (99.305% vs 99.306% once the
tie-off artifacts are excluded). A Trojan hides behind a *rare* trigger
condition, so what matters is whether the test patterns ever drive the rare
nets to their rare value.

* :func:`rare_node_activation` estimates every net's signal probability by
  random simulation, takes the nets whose rarer value occurs in fewer than
  ``threshold`` of random patterns, and counts how many generated patterns
  drive each of them there (the MERO-style N-detect criterion). Rare nets no
  pattern activates are exactly where a trigger can hide undetected.
* :func:`ground_truth_gates` finds the Trojan's own gates by name -- Trust-Hub
  (``Tg1_*``, ``Trojan_*``, ``Trigger_*``, ``*Payload*``) and this tool's
  generator (``HT_TRIGGER_*``, ``HT_PAYLOAD_*``, ``ht_trigger_out_*``) -- so a
  gate selection or suspect list can be scored without a manual comparison.
"""
from __future__ import annotations

import random
import re
from typing import Any, Iterable

from pyatpg.core.netlist import Circuit

from .faultsim import _eval, _levelize, _pack
from .models import TestVectorSet

#: A net is rare when its less likely value shows up in fewer than this share
#: of random patterns.
RARE_THRESHOLD = 0.05
RANDOM_PATTERNS = 4096
RANDOM_SEED = 2024
#: A rare net counts as well tested once this many patterns activate it.
N_DETECT = 5
#: Never-activated rare nets listed in the result (rarest first).
MAX_LISTED = 25

_TROJAN_NAME = re.compile(
    r"(troj|^tg\d|[_.]tg\d|trigger|payl|^ht_|[_.]ht_|tj_)", re.IGNORECASE
)


def ground_truth_gates(circuit: Circuit) -> list[str]:
    """Nets whose names mark them as Trojan logic (trigger, payload, state)."""
    names = set(circuit.gates) | {ff.q for ff in getattr(circuit, "flipflops", [])}
    names |= set(circuit.inputs)  # full scan turns trigger registers into inputs
    return sorted(n for n in names if _TROJAN_NAME.search(n))


def score_selection(selected: Iterable[str], truth: Iterable[str]) -> dict[str, Any]:
    """Precision/recall of a gate list against the known Trojan gates."""
    selected, truth = list(dict.fromkeys(selected)), set(truth)
    hits = [g for g in selected if g in truth]
    return {
        "selected": len(selected),
        "trojan_gates_hit": hits,
        "precision": round(len(hits) / len(selected), 4) if selected else None,
        "recall": round(len(hits) / len(truth), 4) if truth else None,
    }


def _random_probabilities(circuit: Circuit, order: list[str], patterns: int,
                          seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    mask = (1 << patterns) - 1
    # Sorted: a set's order changes with the process's hash seed, and the same
    # seed must give the same patterns (and the same verdict) on every run.
    values = {pi: rng.getrandbits(patterns) for pi in sorted(circuit.inputs)}
    for out in order:
        gate = circuit.gates[out]
        values[out] = _eval(gate.gtype, [values.get(i, 0) for i in gate.inputs], mask)
    return {net: bin(v).count("1") / patterns for net, v in values.items() if net in circuit.gates}


def _pattern_values(circuit: Circuit, order: list[str], vectors: TestVectorSet) -> tuple[dict[str, int], int]:
    n = vectors.count
    mask = (1 << n) - 1
    columns: dict[str, list[str]] = {pi: [] for pi in vectors.primary_inputs}
    for vec in vectors.vectors:
        for name, ch in zip(vectors.primary_inputs, vec.inputs):
            columns[name].append(ch)
    values = {name: _pack(col) for name, col in columns.items()}
    for pi in circuit.inputs:
        values.setdefault(pi, 0)
    for out in order:
        gate = circuit.gates[out]
        values[out] = _eval(gate.gtype, [values.get(i, 0) for i in gate.inputs], mask)
    return values, n


def rare_node_activation(
    circuit: Circuit,
    vectors: TestVectorSet,
    *,
    threshold: float = RARE_THRESHOLD,
    random_patterns: int = RANDOM_PATTERNS,
    seed: int = RANDOM_SEED,
    n_detect: int = N_DETECT,
    watch: Iterable[str] = (),
) -> dict[str, Any]:
    """How well ``vectors`` exercise the circuit's rare nets.

    ``watch`` names nets (e.g. the known Trojan gates) whose activation is
    reported individually whether or not they are rare.
    """
    if vectors.count == 0 or not vectors.primary_inputs:
        return {"unavailable_reason": "The run produced no test patterns to simulate."}
    unknown = [p for p in vectors.primary_inputs if p not in circuit.inputs]
    if unknown:
        return {"unavailable_reason": f"Pattern file names signals not in the circuit ({', '.join(unknown[:5])})."}
    try:
        order = _levelize(circuit)
    except ValueError as exc:
        return {"unavailable_reason": str(exc)}

    probs = _random_probabilities(circuit, order, random_patterns, seed)
    values, n = _pattern_values(circuit, order, vectors)

    rare = []
    for net, p1 in probs.items():
        rare_value, p_rare = (1, p1) if p1 <= 0.5 else (0, 1 - p1)
        if p_rare < threshold:
            rare.append((net, rare_value, p_rare))

    def hits(net: str, rare_value: int) -> int:
        v = values.get(net, 0)
        ones = bin(v).count("1")
        return ones if rare_value else n - ones

    activated = n_ok = 0
    never: list[dict[str, Any]] = []
    for net, rare_value, p_rare in rare:
        count = hits(net, rare_value)
        if count:
            activated += 1
        if count >= n_detect:
            n_ok += 1
        if not count:
            never.append({"net": net, "rare_value": rare_value,
                          "random_probability": round(p_rare, 5)})
    never.sort(key=lambda r: (r["random_probability"], r["net"]))

    watched = []
    for net in watch:
        if net not in circuit.gates:
            continue
        p1 = probs.get(net, 0.5)
        rare_value = 1 if p1 <= 0.5 else 0
        watched.append({
            "net": net,
            "rare_value": rare_value,
            "random_probability": round(p1 if rare_value else 1 - p1, 5),
            "is_rare": min(p1, 1 - p1) < threshold,
            "patterns_activating": hits(net, rare_value),
        })

    total = len(rare)
    return {
        "threshold": threshold,
        "random_patterns": random_patterns,
        "patterns": n,
        "n_detect": n_detect,
        "rare_nodes": total,
        "activated": activated,
        "activation_pct": round(100 * activated / total, 2) if total else None,
        "activated_n_times": n_ok,
        "n_detect_pct": round(100 * n_ok / total, 2) if total else None,
        "never_activated_count": len(never),
        "never_activated": never[:MAX_LISTED],
        "watched": watched,
        "note": (
            f"Rare nets: value seen in under {threshold:.0%} of {random_patterns} random patterns. "
            "A Trojan trigger is built from rare nets, so rare nets no pattern activates are "
            "where a trigger can stay hidden; coverage alone does not show this."
        ),
    }
