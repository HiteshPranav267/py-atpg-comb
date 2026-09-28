"""Trigger-payload candidates, a rule-based ranking, and local context for an LLM judge.

The structural detector in :mod:`pyatpg.atpg.suspects` recognises one trojan
shape: a rarely-satisfied conjunction driving a spliced-in XOR/XNOR. It finds
nothing when the payload is an OR (force-1) or a mux (force-0), and simply
extending its rules to OR/AND gates would flag ordinary rare-enable logic all
over a clean circuit. This module separates the problem into three parts so
that ranking methods can be compared on exactly the same footing:

* :func:`generate_candidates` -- deterministic, deliberately loose: every
  rarely-toggling, single-load gate that feeds (directly or through one
  inverter/buffer) a 2-input gate. Any payload type matches; so does plenty of
  legitimate logic.
* :func:`relaxed_rule_score` -- the hand-designed competitor: trigger rarity
  times the activity imbalance between the payload gate's two inputs.
* :func:`candidate_context` -- an anonymised local subgraph around the
  candidate, for a model to judge. Net names never appear in it.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any

#: A trigger fires rarely. Loose on purpose: candidate generation must not
#: miss real triggers, and the rankers decide what is actually suspicious.
RARE_TOGGLE_MAX = 0.3
_INVERTING_OR_BUFFER = {"NOT", "BUF", "BUFF"}
_EPS = 1e-4


@dataclass
class Candidate:
    """A possible trigger -> payload pair.

    ``trigger`` is the rare gate, ``via`` an optional inverter/buffer between
    it and the payload, ``payload`` the 2-input gate it feeds, and ``other``
    the payload's other input (the signal a trojan would be corrupting).
    """

    trigger: str
    payload: str
    other: str
    via: str | None = None
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.trigger}>{self.via or ''}>{self.payload}"


def _features(profiles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(p["gate_id"]): (p.get("features") or {}) for p in profiles}


def generate_candidates(profiles: list[dict[str, Any]], graph: Any) -> list[Candidate]:
    """Every rare, single-load gate feeding a 2-input gate (optionally via an inverter)."""
    G = graph.G
    feats = _features(profiles)
    out: list[Candidate] = []
    for t, f in feats.items():
        if t not in G or f.get("node_type") == "PI":
            continue
        toggle = f.get("toggle_rate")
        if toggle is None or toggle > RARE_TOGGLE_MAX:
            continue
        if (f.get("fan_in") or 0) < 2 or G.out_degree(t) != 1:
            continue
        (succ,) = G.successors(t)
        via = None
        sf = feats.get(str(succ), {})
        if (sf.get("gate_type") or "").upper() in _INVERTING_OR_BUFFER and G.out_degree(succ) == 1:
            via = str(succ)
            (succ,) = G.successors(succ)
        payload = str(succ)
        preds = [str(x) for x in G.predecessors(payload)]
        if len(preds) != 2:
            continue
        driver = via or t
        if driver not in preds:
            continue
        other = preds[0] if preds[1] == driver else preds[1]
        out.append(Candidate(trigger=t, payload=payload, other=other, via=via))
    return out


def relaxed_rule_score(c: Candidate, profiles_or_feats: Any) -> float:
    """Trigger rarity x input-activity imbalance, in log space.

    The natural generalisation of the XOR rule to any payload gate type: a
    trigger that almost never toggles, feeding a gate whose other input is an
    ordinary active signal. Higher = more suspicious.
    """
    feats = profiles_or_feats if isinstance(profiles_or_feats, dict) else _features(profiles_or_feats)
    t_rate = float(feats.get(c.trigger, {}).get("toggle_rate", 0.5))
    o_rate = float(feats.get(c.other, {}).get("toggle_rate", 0.5))
    rarity = -math.log10(t_rate + _EPS)
    imbalance = math.log10((o_rate + _EPS) / (t_rate + _EPS))
    return rarity + max(0.0, imbalance)


# ----------------------------------------------------------------------
# Local context for the LLM judge
# ----------------------------------------------------------------------

def _log2_bucket(v: Any) -> int:
    """SCOAP values bucketed to powers of two: the magnitude is what matters,
    and exact values shift across a whole cone whenever any gate is added."""
    v = int(v or 0)
    return 0 if v <= 0 else v.bit_length()


def _node_row(feats: dict[str, Any], scoap: bool) -> dict[str, Any]:
    row = {
        "type": feats.get("gate_type") or ("INPUT" if feats.get("node_type") == "PI" else "?"),
        "role": {"PI": "primary-input", "PO": "primary-output"}.get(feats.get("node_type"), "internal"),
        "toggle": round(float(feats.get("toggle_rate", 0.5)), 4),
        "fan_in": feats.get("fan_in", 0),
        "fan_out": feats.get("fan_out", 0),
    }
    if scoap:
        row.update({"cc0~2^": _log2_bucket(feats.get("cc0")), "cc1~2^": _log2_bucket(feats.get("cc1")),
                    "co~2^": _log2_bucket(feats.get("co")), "reconv": feats.get("reconvergence", 0)})
    return row


def candidate_context(
    c: Candidate,
    profiles_or_feats: Any,
    graph: Any,
    variant: str = "C",
    hops: int = 2,
    max_nodes: int = 28,
) -> tuple[str, str]:
    """Anonymised text description of the candidate's neighbourhood.

    ``variant`` selects how much the judge sees, for the ablation:
      * "A" -- the candidate's own three or four gates, statistics only;
      * "B" -- plus the ``hops``-hop neighbourhood and its connections;
      * "C" -- B plus SCOAP controllability/observability (log2-bucketed)
        and reconvergence.

    Returns (canonical_key, text). Gate ids are replaced by ``n0, n1, ...``
    assigned in a shuffled order seeded by the canonical content, so the same
    structure always yields the same prompt (cacheable), yet no id encodes
    insertion order or original names.
    """
    G = graph.G
    feats = profiles_or_feats if isinstance(profiles_or_feats, dict) else _features(profiles_or_feats)
    core = [c.trigger] + ([c.via] if c.via else []) + [c.payload, c.other]

    nodes: list[str] = list(dict.fromkeys(core))
    if variant in ("B", "C"):
        frontier = list(nodes)
        for _ in range(hops):
            nxt = []
            for n in frontier:
                for m in list(G.predecessors(n)) + list(G.successors(n)):
                    m = str(m)
                    if m not in nodes and len(nodes) < max_nodes:
                        nodes.append(m)
                        nxt.append(m)
            frontier = nxt
    node_set = set(nodes)
    scoap = variant == "C"

    rows = {n: _node_row(feats.get(n, {}), scoap) for n in nodes}
    if variant in ("B", "C"):
        # Depth relative to the shallowest node shown: absolute logic level
        # shifts for a whole downstream cone whenever any gate is inserted,
        # while the local shape -- which nodes sit deeper than which -- does not.
        base = min(int(feats.get(n, {}).get("level", 0) or 0) for n in nodes)
        for n in nodes:
            rows[n]["depth"] = int(feats.get(n, {}).get("level", 0) or 0) - base
    edges = sorted((str(a), str(b)) for a, b in G.subgraph(nodes).edges()) if variant != "A" else [
        (a, b) for a, b in zip(core, core[1:]) if G.has_edge(a, b)
    ] + ([(c.other, c.payload)] if G.has_edge(c.other, c.payload) else [])
    roles = {c.trigger: "suspected-trigger", c.payload: "suspected-payload",
             c.other: "payload-other-input"}
    if c.via:
        roles[c.via] = "between-trigger-and-payload"

    # Canonical form: structure + statistics, independent of net names.
    canon_nodes = sorted(
        (roles.get(n, ""), json.dumps(rows[n], sort_keys=True),
         sorted(json.dumps(rows[p], sort_keys=True) for p in G.predecessors(n) if str(p) in node_set))
        for n in nodes
    )
    canon = json.dumps({"v": variant, "nodes": canon_nodes, "n_edges": len(edges)}, sort_keys=True)
    key = hashlib.sha256(canon.encode()).hexdigest()[:24]

    order = list(nodes)
    random.Random(int(key[:8], 16)).shuffle(order)
    alias = {n: f"n{i}" for i, n in enumerate(order)}

    # Compact table: one header, one short row per gate. Roughly half the
    # tokens of key=value text, which is what bounds how much a quota-limited
    # evaluation can afford.
    role_code = {"suspected-trigger": "T", "between-trigger-and-payload": "V",
                 "suspected-payload": "P", "payload-other-input": "O"}
    cols = [k for k in rows[nodes[0]] if k not in ("type", "role")]
    lines = ["id mark type where " + " ".join(cols) + " inputs"]
    for n in order:
        r = rows[n]
        where = {"primary-input": "PI", "primary-output": "PO"}.get(r["role"], "int")
        ins = [alias[str(p)] for p in G.predecessors(n) if str(p) in node_set]
        outside_in = sum(1 for p in G.predecessors(n) if str(p) not in node_set)
        src = ",".join(ins) + (f"{'+' if ins else ''}{outside_in}ext" if outside_in else "")
        vals = " ".join(str(r[k]) for k in cols)
        lines.append(f"{alias[n]} {role_code.get(roles.get(n, ''), '-')} {r['type']} {where} {vals} {src or '-'}")
    return key, "\n".join(lines)


def is_trojan_candidate(c: Candidate, metadata: dict[str, Any]) -> bool:
    """Ground truth: the candidate's trigger is the inserted trigger and its payload the spliced gate."""
    return c.trigger == metadata.get("trigger_output") and c.payload == metadata.get("payload_target")
