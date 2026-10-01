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
* :func:`candidate_facts` -- structural facts about a candidate (how unusual
  its rarity is in this circuit, whether the trigger shares its inputs with
  sibling gates, whether the payload continues a signal that now feeds only
  it, how far its effect reaches). Context "D" shows them to the LLM, and
  :mod:`pyatpg.evaluation.trojan_feature_baseline` fits a plain classifier
  on exactly the same numbers, so any LLM gain over it is the model's own.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics

import networkx as nx
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
    stats: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Anonymised text description of the candidate's neighbourhood.

    ``variant`` selects how much the judge sees, for the ablation:
      * "A" -- the candidate's own three or four gates, statistics only;
      * "B" -- plus the ``hops``-hop neighbourhood and its connections;
      * "C" -- B plus SCOAP controllability/observability (log2-bucketed)
        and reconvergence;
      * "D" -- B plus :func:`candidate_facts` as labelled lines: rarity
        relative to the circuit, trigger siblings, payload type frequency,
        the corrupted signal's fanout, downstream reach and local outliers.
        ``stats`` (from :func:`circuit_stats`) saves recomputing them per
        candidate.

    Returns (canonical_key, text). Gate ids are replaced by ``n0, n1, ...``
    assigned in a shuffled order seeded by the canonical content, so the same
    structure always yields the same prompt (cacheable), yet no id encodes
    insertion order or original names.
    """
    G = graph.G
    feats = profiles_or_feats if isinstance(profiles_or_feats, dict) else _features(profiles_or_feats)
    core = [c.trigger] + ([c.via] if c.via else []) + [c.payload, c.other]

    facts_block = ""
    if variant == "D":
        facts = candidate_facts(c, feats, graph, stats or circuit_stats(feats, graph))
        facts_block = facts_text(facts)
        variant_table = "B"
    else:
        variant_table = variant

    nodes: list[str] = list(dict.fromkeys(core))
    if variant_table in ("B", "C"):
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
    scoap = variant_table == "C"

    rows = {n: _node_row(feats.get(n, {}), scoap) for n in nodes}
    if variant_table in ("B", "C"):
        # Depth relative to the shallowest node shown: absolute logic level
        # shifts for a whole downstream cone whenever any gate is inserted,
        # while the local shape -- which nodes sit deeper than which -- does not.
        base = min(int(feats.get(n, {}).get("level", 0) or 0) for n in nodes)
        for n in nodes:
            rows[n]["depth"] = int(feats.get(n, {}).get("level", 0) or 0) - base
    edges = sorted((str(a), str(b)) for a, b in G.subgraph(nodes).edges()) if variant_table != "A" else [
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
    canon_obj = {"v": variant, "nodes": canon_nodes, "n_edges": len(edges)}
    if facts_block:  # absent for A/B/C, so their keys (and cached answers) are unchanged
        canon_obj["facts"] = facts_block
    canon = json.dumps(canon_obj, sort_keys=True)
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
    if facts_block:
        lines.append(facts_block)
    return key, "\n".join(lines)


def is_trojan_candidate(c: Candidate, metadata: dict[str, Any]) -> bool:
    """Ground truth: the candidate's trigger is the inserted trigger and its payload the spliced gate."""
    return c.trigger == metadata.get("trigger_output") and c.payload == metadata.get("payload_target")


# ----------------------------------------------------------------------
# Structural facts (context "D" and the feature baseline)
# ----------------------------------------------------------------------

def circuit_stats(profiles_or_feats: Any, graph: Any) -> dict[str, Any]:
    """Per-circuit reference values the facts are measured against."""
    G = graph.G
    feats = profiles_or_feats if isinstance(profiles_or_feats, dict) else _features(profiles_or_feats)
    gates = [n for n, f in feats.items() if n in G and f.get("node_type") != "PI"]
    toggles = sorted(float(feats[n].get("toggle_rate", 0.5)) for n in gates)
    types: dict[str, int] = {}
    for n in gates:
        t = (feats[n].get("gate_type") or "?").upper()
        types[t] = types.get(t, 0) + 1
    pos = {n for n, f in feats.items() if f.get("node_type") == "PO"} | {
        n for n in G if G.out_degree(n) == 0}
    return {"toggles": toggles, "types": types, "n_gates": max(1, len(gates)), "pos": pos}


def _share_at_most(sorted_vals: list[float], x: float) -> float:
    """Fraction of values <= x (sorted input)."""
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo / max(1, len(sorted_vals))


def _within(G, start: str, hops: int) -> set[str]:
    seen, frontier = {start}, [start]
    for _ in range(hops):
        nxt = []
        for n in frontier:
            for m in list(G.predecessors(n)) + list(G.successors(n)):
                if m not in seen:
                    seen.add(m)
                    nxt.append(m)
        frontier = nxt
    seen.discard(start)
    return {str(x) for x in seen}


def candidate_facts(
    c: Candidate, profiles_or_feats: Any, graph: Any, stats: dict[str, Any]
) -> dict[str, float]:
    """Numbers describing how out of place a candidate is. All net-name free.

    Trigger: its toggle rate, and what share of the circuit's gates are at
    least as rare (rarity is weak evidence where rare gates are common);
    how many other gates read two or more of its inputs (a decoder or
    comparator has siblings, a spliced trigger usually none); whether its
    inputs come from very different depths.
    Payload: how common its gate type is in the circuit and around it;
    whether the other input -- the signal a trojan would corrupt -- now
    feeds only this payload (a spliced gate takes over its loads); how many
    primary outputs it reaches and how far away they are.
    Local outlier: the trigger's toggle against the median of its
    two-hop neighbourhood.
    """
    G = graph.G
    feats = profiles_or_feats if isinstance(profiles_or_feats, dict) else _features(profiles_or_feats)

    def f(n: str) -> dict[str, Any]:
        return feats.get(n, {})

    t_tog = float(f(c.trigger).get("toggle_rate", 0.5))
    t_inputs = [str(x) for x in G.predecessors(c.trigger)]
    in_levels = [int(f(x).get("level", 0) or 0) for x in t_inputs]
    in_set = set(t_inputs)
    siblings = 0
    candidates_seen: set[str] = set()
    for x in t_inputs:
        for g in G.successors(x):
            g = str(g)
            if g == c.trigger or g in candidates_seen:
                continue
            candidates_seen.add(g)
            if sum(1 for y in G.predecessors(g) if str(y) in in_set) >= 2:
                siblings += 1

    p_type = (f(c.payload).get("gate_type") or "?").upper()
    near_p = _within(G, c.payload, 2) - {c.trigger, c.via or "", c.other}
    same_type_near = sum(1 for n in near_p if (f(n).get("gate_type") or "?").upper() == p_type)
    dist = nx.single_source_shortest_path_length(G, c.payload)
    to_po = [d for n, d in dist.items() if n in stats["pos"]]
    reach = len(to_po)

    near_t = [n for n in _within(G, c.trigger, 2) - {c.payload, c.via or "", c.other}
              if f(n).get("node_type") != "PI"]
    neigh_tog = [float(f(n).get("toggle_rate", 0.5)) for n in near_t]

    return {
        "trigger_toggle": round(t_tog, 4),
        "share_gates_as_rare": round(_share_at_most(stats["toggles"], t_tog + 1e-9), 3),
        "trigger_fan_in": len(t_inputs),
        "trigger_siblings": siblings,
        "trigger_input_depth_spread": (max(in_levels) - min(in_levels)) if in_levels else 0,
        "via_inverter": int(c.via is not None),
        "payload_type_share_circuit": round(stats["types"].get(p_type, 0) / stats["n_gates"], 3),
        "payload_type_share_nearby": round(same_type_near / max(1, len(near_p)), 2),
        "payload_fan_out": G.out_degree(c.payload),
        "payload_outputs_reached": reach,
        # Shortest path to any primary output; -1 when none is reachable.
        "payload_levels_to_output": min(to_po) if to_po else -1,
        "other_toggle": round(float(f(c.other).get("toggle_rate", 0.5)), 4),
        "other_fan_out": G.out_degree(c.other),
        "neighbourhood_median_toggle": round(statistics.median(neigh_tog), 3) if neigh_tog else 0.5,
    }


def _bucket(v: float, step: float) -> float:
    return round(round(v / step) * step, 4)


def facts_text(facts: dict[str, float]) -> str:
    """The facts as one compact line for the LLM; the prompt defines each label.

    Circuit-wide shares are bucketed coarsely: inserting a trojan nudges them
    by a gate or two, and exact values would make every variant's context
    unique -- defeating the judgement cache -- without telling the model
    anything a coarser value does not.
    """
    pct = lambda v: f"{round(100 * v)}%"  # noqa: E731
    reach = facts["payload_outputs_reached"]
    # Bands, not counts: a cone's exact output count is not the point, its scale is.
    reach_band = next(label for limit, label in ((0, "0"), (1, "1"), (2, "2"), (5, "3-5"),
                                                  (10, "6-10"), (20, "11-20"), (10**9, "20+"))
                      if reach <= limit)
    d = facts["payload_levels_to_output"]
    dist_band = "none" if d < 0 else next(label for limit, label in (
        (0, "0"), (2, "1-2"), (5, "3-5"), (10, "6-10"), (10**9, "11+")) if d <= limit)
    return (
        "facts: "
        f"T_rarer_than {pct(_bucket(1 - facts['share_gates_as_rare'], 0.05))} | "
        f"T_siblings {facts['trigger_siblings']} | "
        f"T_in_depth_spread {facts['trigger_input_depth_spread']} | "
        f"link {'inv' if facts['via_inverter'] else 'direct'} | "
        f"P_type_share {pct(_bucket(facts['payload_type_share_circuit'], 0.02))} circuit, "
        f"{pct(_bucket(facts['payload_type_share_nearby'], 0.1))} nearby | "
        f"P_fan_out {facts['payload_fan_out']} | "
        f"P_reach {reach_band} PO, nearest {dist_band} lv | "
        f"O_fan_out {facts['other_fan_out']} | "
        f"neigh_median_toggle {round(facts['neighbourhood_median_toggle'], 2)}"
    )
