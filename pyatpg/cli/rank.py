"""``pyatpg rank``: the ranked rare cones and the targeted fault list, without ATPG.

This is the method's core output on its own. It runs the same steps as a full
analysis up to the fault list (parse, full-scan model, profile, detectors,
rare cones, the structural ranking alternated with rarity order) and stops
there, so it needs no ATALANTA and takes seconds. The list is exactly the one
``pyatpg run`` hands ATALANTA when no language model is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RankedCone:
    place: int            #: place in the ranked order (1 = taken first)
    root: str
    gates: int
    taps: int
    fires_pct: float      #: how often the root takes its rarer value, in %
    rarity_place: int     #: place in plain rarity order (1 = rarest)
    score: float          #: structural score (first template)
    rare_inputs: float    #: share of taps that are rare nets (second template)
    picked_by: str        #: "score template", "rare-input template" or "rarity order"
    in_list: bool         #: its root is in the targeted fault list
    trojan_gates: int = 0  #: known Trojan gates inside it (0 when none are known)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RankResult:
    name: str
    gates: int
    inputs: int
    outputs: int
    flip_flops: int
    full_scan: bool
    rare_cones: int
    fault_list: list[str]
    cones: list[RankedCone]
    notes: list[str] = field(default_factory=list)
    #: Only when the netlist's Trojan gates are known by name (an answer key).
    trojan_gates: int = 0
    orderings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["cones"] = [c.to_dict() for c in self.cones]
        return data


def _labelled_order(candidates: list[Any]) -> list[tuple[Any, str]]:
    """The no-model ordering with the source of each cone (mirrors targeting._alternate)."""
    from itertools import zip_longest

    from pyatpg.atpg import cones as cone_mod

    by_score = sorted(candidates, key=lambda c: (-cone_mod.structural_score(c.features), c.root))
    if cone_mod._SCORE_MODE == "two":
        by_rare = sorted(candidates, key=lambda c: (-float(c.features.get("rare_leaves", 0.0)),
                                                    -cone_mod.structural_score(c.features), c.root))
        ranked, seen = [], set()
        for a, b in zip(by_score, by_rare):
            for c, label in ((a, "score template"), (b, "rare-input template")):
                if c.root not in seen:
                    seen.add(c.root)
                    ranked.append((c, label))
    else:
        ranked = [(c, "score template") for c in by_score]
    out, seen = [], set()
    for a, b in zip_longest(ranked, candidates):
        for item in (a, (b, "rarity order") if b is not None else None):
            if item is not None and item[0].root not in seen:
                seen.add(item[0].root)
                out.append(item)
    return out


def rank_netlist(path: str, toggle_patterns: int = 3000) -> RankResult:
    """Rank the rare cones of the netlist at ``path`` (full-scan view for sequential circuits)."""
    from pyatpg.atpg.cones import find_cones, structural_score
    from pyatpg.atpg.faultsim import _levelize
    from pyatpg.atpg.service import _score_orderings
    from pyatpg.atpg.structural import parse_circuit, profile_circuit
    from pyatpg.atpg.targeting import FaultTargetSelector
    from pyatpg.atpg.trigger_detector import mark_profiles, static_evidence
    from pyatpg.atpg.trojan_metrics import _random_probabilities, ground_truth_gates
    from pyatpg.core import settings
    from pyatpg.core.rtl import read_path
    from pyatpg.core.scan import to_full_scan

    source_text, filename = read_path(path)
    parsed = parse_circuit(source_text, filename)
    notes = list(parsed.parse_notes)
    flip_flops = len(parsed.circuit.flipflops)
    if flip_flops:
        parsed.circuit, counts = to_full_scan(parsed.circuit)
        parsed.derived = True
        notes.append(f"Ranked on the full-scan model: {counts['flip_flops']} flip-flop(s) "
                     "treated as scan cells (rare cones are defined on the combinational core).")
    profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
    if not profile.statistics.get("is_dag", True):
        raise ValueError("The netlist has a combinational loop with no flip-flop breaking it.")

    try:
        use_detector = bool(settings.get("detector_in_targeting"))
    except Exception:  # noqa: BLE001
        use_detector = True
    evidence = static_evidence(parsed.circuit, getattr(parsed.circuit, "logic_clocks", {}))
    if use_detector:
        mark_profiles(profile.gate_profiles, evidence)
    forced = [(g, evidence.by_gate[g][0][1]) for g in evidence.gates()] if use_detector else None
    force_shape = not any(evidence.strength(g) >= 0.7 for g in evidence.gates())

    selection = FaultTargetSelector(provider=None).select(
        profile.gate_profiles, summary="", graph=profile.graph, extra_suspects=forced,
        use_detector=use_detector, force_shape=force_shape, circuit=parsed.circuit,
    )
    notes.extend(selection.notes)
    in_list = set(selection.gates)

    probs = _random_probabilities(parsed.circuit, _levelize(parsed.circuit), 4096, 2024)
    cones = find_cones(parsed.circuit, probs)
    rarity_place = {c.root: i for i, c in enumerate(cones, 1)}
    truth = set(ground_truth_gates(parsed.circuit, parsed.implicit_inputs))

    ranked: list[RankedCone] = []
    order = selection.orderings.get("R+S")
    if order:
        roots = [gates[0] for gates in order[1] if gates]
        by_root = {c.root: c for c in cones}
        candidates = [c for c in cones if c.root in set(roots)]
        labelled = _labelled_order(candidates)
        if [c.root for c, _ in labelled] != roots:  # drifted from targeting: no labels
            labelled = [(by_root[r], "-") for r in roots if r in by_root]
        for place, (cone, label) in enumerate(labelled, 1):
            f = cone.features
            ranked.append(RankedCone(
                place=place, root=cone.root, gates=len(cone.gates), taps=len(cone.leaves),
                fires_pct=round(100 * float(f.get("root_rare", 0.0)), 2),
                rarity_place=rarity_place.get(cone.root, 0),
                score=structural_score(f), rare_inputs=float(f.get("rare_leaves", 0.0)),
                picked_by=label, in_list=cone.root in in_list,
                trojan_gates=len(truth.intersection(cone.gates)),
            ))

    result = RankResult(
        name=parsed.name, gates=len(parsed.circuit.gates), inputs=len(parsed.circuit.inputs),
        outputs=len(parsed.circuit.outputs), flip_flops=flip_flops, full_scan=bool(flip_flops),
        rare_cones=len(cones), fault_list=list(selection.gates), cones=ranked, notes=notes,
    )
    if truth and selection.orderings:
        scored = _score_orderings(selection.orderings, truth)
        result.trojan_gates = scored["trojan_gates"]
        result.orderings = scored["lists"]
    return result
