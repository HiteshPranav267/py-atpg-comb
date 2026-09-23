"""Measure which structural features survive renaming a netlist's identifiers.

The framework's claim to obfuscation resistance rests on features being
computed from graph topology rather than from source text, so renaming every
wire should leave them unchanged. That is an empirical claim about this
implementation, not a theorem: a feature computed from topology can still
depend on identifiers if anything in its computation orders nodes by name.

Two renamings are applied, because they fail differently and conflating them
would misattribute the cause:

*order-preserving* gives every identifier a fresh name while keeping the
sorted order of the originals, so anything that merely reads names is
unaffected and only a genuine text dependence shows up.

*order-scrambling* assigns names whose sort order is unrelated to the
original, which is what real renaming obfuscation does. A feature that is
invariant under the first but not the second depends on name *order* -- for
example a simulation that hands its random stimulus to inputs in sorted
order, where reordering the names redistributes the stimulus.

Only these two transformations are tested. Neither reorders gates, changes
gate types, restructures logic, nor flattens hierarchy, so results here say
nothing about obfuscations that do.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

from pyatpg.core.graph_analysis import CircuitGraph
from pyatpg.core.netlist import Circuit, FlipFlop, ScanFlipFlop

#: Features compared per gate. Each maps a name to a callable pulling that
#: value out of one gate's feature dict, so a mismatch is reported per
#: feature rather than as one opaque "profiles differ".
COMPARED_FEATURES: dict[str, Callable[[dict], Any]] = {
    "scoap_cc0": lambda f: f.get("cc0"),
    "scoap_cc1": lambda f: f.get("cc1"),
    "scoap_co": lambda f: f.get("co"),
    "toggle_rate": lambda f: f.get("toggle_rate"),
    "centrality": lambda f: f.get("centrality"),
    "level": lambda f: f.get("level"),
    "fan_in": lambda f: f.get("fan_in"),
    "fan_out": lambda f: f.get("fan_out"),
    "fan_ratio": lambda f: f.get("fan_ratio"),
    "neighbor_diversity": lambda f: f.get("neighbor_diversity"),
    "gate_type": lambda f: f.get("gate_type"),
}

#: Floats are compared to this tolerance; anything coarser would hide a real
#: divergence in toggle rate, anything finer trips on representation noise.
FLOAT_TOLERANCE = 1e-9


@dataclass
class RenameMap:
    """A bijection between original and renamed identifiers."""

    forward: dict[str, str]
    mode: str

    @property
    def reverse(self) -> dict[str, str]:
        return {new: old for old, new in self.forward.items()}

    def __post_init__(self) -> None:
        if len(set(self.forward.values())) != len(self.forward):
            raise ValueError("rename map is not injective; features could not be mapped back")


@dataclass
class FeatureComparison:
    """Per-feature invariance over every gate common to both circuits."""

    feature: str
    compared: int = 0
    matches: int = 0
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def invariant(self) -> bool:
        return self.compared > 0 and self.matches == self.compared

    @property
    def match_rate(self) -> float:
        return self.matches / self.compared if self.compared else 0.0


@dataclass
class InvarianceReport:
    """What survived one renaming of one circuit."""

    circuit: str
    mode: str
    gates_compared: int
    features: dict[str, FeatureComparison]
    ranking_overlap: float | None = None
    ranking_identical: bool | None = None
    ranking_size: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def invariant_features(self) -> list[str]:
        return sorted(n for n, c in self.features.items() if c.invariant)

    @property
    def varying_features(self) -> list[str]:
        return sorted(n for n, c in self.features.items() if not c.invariant)


def _ordered_names(circuit: Circuit) -> list[str]:
    """Every identifier, in a deterministic order."""
    return sorted(str(n) for n in circuit.nodes)


def build_rename_map(circuit: Circuit, mode: str = "scramble",
                     seed: int = 20260920) -> RenameMap:
    """Map every identifier to a fresh one.

    ``order-preserve`` keeps the sorted position of each name (the i-th name
    alphabetically becomes the i-th new name alphabetically, zero-padded so
    the lexicographic order matches the numeric one). ``scramble`` shuffles
    that assignment, so sorted order carries no information from the original.
    """
    names = _ordered_names(circuit)
    width = max(4, len(str(len(names))))
    targets = [f"sig{i:0{width}d}" for i in range(len(names))]

    if mode == "scramble":
        random.Random(seed).shuffle(targets)
    elif mode != "order-preserve":
        raise ValueError(f"unknown rename mode {mode!r}")

    return RenameMap(forward=dict(zip(names, targets)), mode=mode)


def rename_circuit(circuit: Circuit, mapping: RenameMap) -> Circuit:
    """A structurally identical circuit with every identifier replaced.

    Rebuilt through the public constructors rather than by mutating a clone,
    so a name that the mapping missed surfaces as a KeyError here instead of
    as a silently half-renamed circuit that still compares equal.
    """
    forward = mapping.forward
    renamed = Circuit()

    for name in sorted(circuit.inputs):
        renamed.add_input(forward[str(name)])
    for name in sorted(circuit.outputs):
        renamed.add_output(forward[str(name)])
    for output in sorted(circuit.gates):
        gate = circuit.gates[output]
        renamed.add_gate(
            gtype=gate.gtype,
            output=forward[str(output)],
            inputs=[forward[str(i)] for i in gate.inputs],
        )
    for ff in circuit.flipflops:
        if getattr(ff, "is_scan", False):
            renamed.add_flipflop(ScanFlipFlop(
                q=forward[str(ff.q)], d=forward[str(ff.d)],
                si=forward[str(ff.si)], se=forward[str(ff.se)],
            ))
        else:
            renamed.add_flipflop(FlipFlop(q=forward[str(ff.q)], d=forward[str(ff.d)]))
    for name in sorted(circuit.wires):
        renamed.wires.add(forward[str(name)])
    return renamed


def _values_match(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b
        return abs(float(a) - float(b)) <= FLOAT_TOLERANCE
    return a == b


def _extract(circuit: Circuit, toggle_patterns: int, toggle_seed: int):
    """Features and profiles for one circuit, from one profiling pass."""
    graph = CircuitGraph(circuit)
    graph.compute_toggle_rates(num_patterns=toggle_patterns, seed=toggle_seed)
    return graph.get_gate_features(), graph


def _heuristic_ranking(graph: CircuitGraph) -> list[str]:
    """Gates the pipeline's own deterministic fallback would target.

    ``FaultTargetSelector`` with no provider is the exact path a targeted run
    takes when no API key is configured, so this measures the shipped
    heuristic rather than a reimplementation of it. An LLM ranker is
    deliberately not used: its output is not reproducible enough to attribute
    a difference to renaming rather than to sampling.
    """
    from pyatpg.atpg.targeting import FaultTargetSelector

    selector = FaultTargetSelector(provider=None)
    selection = selector.select(graph.to_llm_feature_profiles(), summary="", graph=graph)
    return list(selection.gates)


def compare_invariance(
    circuit: Circuit,
    circuit_name: str,
    mode: str = "scramble",
    seed: int = 20260920,
    toggle_patterns: int = 3000,
    toggle_seed: int = 20250822,
) -> InvarianceReport:
    """Extract features before and after renaming and compare them.

    Renamed gates are mapped back through the bijection before comparison,
    so a feature is only counted as matching when the *same physical gate*
    carries the same value under both namings.
    """
    mapping = build_rename_map(circuit, mode=mode, seed=seed)
    renamed = rename_circuit(circuit, mapping)

    before, graph_before = _extract(circuit, toggle_patterns, toggle_seed)
    after_renamed, graph_after = _extract(renamed, toggle_patterns, toggle_seed)

    forward = mapping.forward
    # Map the renamed circuit's features back into the original namespace.
    after: dict[str, dict] = {}
    unmapped = 0
    for new_name, feats in after_renamed.items():
        original = mapping.reverse.get(str(new_name))
        if original is None:
            unmapped += 1
            continue
        after[original] = feats

    common = sorted(set(before) & set(after))
    comparisons = {name: FeatureComparison(feature=name) for name in COMPARED_FEATURES}

    for gate_id in common:
        for name, extract in COMPARED_FEATURES.items():
            lhs, rhs = extract(before[gate_id]), extract(after[gate_id])
            if lhs is None and rhs is None:
                continue
            comp = comparisons[name]
            comp.compared += 1
            if _values_match(lhs, rhs):
                comp.matches += 1
            elif len(comp.mismatches) < 5:      # a sample is enough to diagnose
                comp.mismatches.append(
                    {"gate": gate_id, "before": lhs, "after": rhs}
                )

    notes: list[str] = []
    if unmapped:
        notes.append(f"{unmapped} renamed gate(s) had no entry in the rename map")
    missing = (set(before) | set(after)) - set(common)
    if missing:
        notes.append(f"{len(missing)} gate(s) present in only one of the two circuits")

    try:
        rank_before = _heuristic_ranking(graph_before)
        rank_after = [
            mapping.reverse.get(str(g), str(g)) for g in _heuristic_ranking(graph_after)
        ]
        # Set overlap, not sequence equality, is the meaningful measure: the
        # selection is a fault list, and which gates are in it matters while
        # their order does not.
        overlap = (len(set(rank_before) & set(rank_after)) / len(rank_before)
                   if rank_before else None)
        identical = set(rank_before) == set(rank_after)
        size = len(rank_before)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        notes.append(f"ranking comparison unavailable: {type(exc).__name__}: {exc}")
        overlap, identical, size = None, None, 0

    return InvarianceReport(
        circuit=circuit_name,
        mode=mapping.mode,
        gates_compared=len(common),
        features=comparisons,
        ranking_overlap=overlap,
        ranking_identical=identical,
        ranking_size=size,
        notes=notes,
    )
