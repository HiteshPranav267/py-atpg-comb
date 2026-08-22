"""Deterministic structural detection of trojan-shaped logic.

This runs *alongside* the LLM selector in targeted mode, not instead of it.
The LLM ranks gates by how hard they are to test; this looks for a specific
structural shape instead, so a gate can be flagged here even when its
testability numbers are unremarkable.

Why both: measured over the project's ISCAS-85 trojan corpus, ranking gates
purely by testability extremity missed inserted logic whose toggle rate and
observability sat mid-distribution -- on c432, where every gate was visible
to the model, half the planted trojans were still not selected. Those misses
are not an information problem the model can reason its way out of; the
signal that identifies them is structural, and is computed here.

What it looks for is the shape a combinational trojan has, independent of
naming: a rarely-satisfied conjunction (the trigger) feeding a 2-input
XOR/XNOR (the payload) that has been spliced into an existing path. Nothing
here inspects signal names -- a detector keyed on ``ht_`` or ``trojan``
prefixes would score perfectly on this corpus and tell you nothing about a
real adversary, who names their nets whatever the surrounding netlist uses.
"""
from __future__ import annotations

from typing import Any

#: A trigger must fire rarely to be stealthy. Generous relative to the
#: 0.1 used for general "hard to test" filtering: a real trigger is far
#: rarer than this, and the cost of a false positive here is a couple of
#: extra faults in the list.
_TRIGGER_MAX_TOGGLE = 0.25

#: How much rarer the trigger input of a candidate payload XOR must be than
#: its data input. A payload XOR sits on a normal signal path, so its data
#: side toggles at ordinary rates while its trigger side almost never does.
_PAYLOAD_TOGGLE_RATIO = 3.0

#: Conjunctions with more inputs are rarer and so more trigger-like; below
#: this a gate is ordinary combinational logic.
_MIN_TRIGGER_FAN_IN = 2


def _feat(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("features", {}) or {}


def find_structural_suspects(
    profiles: list[dict[str, Any]], graph: Any
) -> tuple[list[str], list[str]]:
    """Return (gate_ids, human-readable reasons) for trojan-shaped logic.

    Args:
        profiles: gate profiles from ``CircuitGraph.to_llm_feature_profiles``.
        graph: the ``CircuitGraph`` those profiles came from, for topology.

    Returns:
        A list of suspect gate ids and a parallel list of one-line reasons.
        Both are empty when nothing matches, which is the expected result on
        a clean circuit.
    """
    by_id = {str(p["gate_id"]): p for p in profiles}
    G = getattr(graph, "G", None)
    if G is None:
        return [], []

    suspects: dict[str, str] = {}

    for gate_id, profile in by_id.items():
        if gate_id not in G:
            continue
        f = _feat(profile)
        gtype = (f.get("gate_type") or "").upper()

        # --- payload shape: 2-input XOR/XNOR with one near-static input ---
        if gtype in ("XOR", "XNOR"):
            preds = [str(p) for p in G.predecessors(gate_id)]
            if len(preds) == 2:
                rates = []
                for p in preds:
                    pf = _feat(by_id.get(p, {}))
                    rates.append((p, pf.get("toggle_rate")))
                if all(r is not None for _, r in rates):
                    (a, ra), (b, rb) = rates
                    rare, common = ((a, ra), (b, rb)) if ra <= rb else ((b, rb), (a, ra))
                    rare_id, rare_rate = rare
                    _common_id, common_rate = common
                    # A genuine data path XOR has both inputs toggling at
                    # comparable rates. A spliced-in payload does not.
                    if rare_rate <= _TRIGGER_MAX_TOGGLE and rare_rate > 0:
                        if common_rate >= rare_rate * _PAYLOAD_TOGGLE_RATIO:
                            suspects.setdefault(
                                gate_id,
                                f"{gtype} with one rarely-toggling input "
                                f"({rare_id} at {rare_rate:.4f}) against a normally "
                                f"active one ({common_rate:.4f}) -- the shape of a "
                                f"payload spliced into an existing path",
                            )
                            suspects.setdefault(
                                rare_id,
                                f"drives the rare input of {gtype} {gate_id}, "
                                f"consistent with a trigger feeding a payload",
                            )

        # --- trigger shape: rarely-satisfied conjunction, narrow fan-out ---
        if gtype in ("AND", "NAND"):
            toggle = f.get("toggle_rate")
            fan_in = f.get("fan_in") or 0
            fan_out = f.get("fan_out") or 0
            if (
                toggle is not None
                and toggle <= _TRIGGER_MAX_TOGGLE
                and fan_in >= _MIN_TRIGGER_FAN_IN
                and fan_out == 1
            ):
                # Only interesting if what it drives is a payload-shaped gate;
                # rarely-toggling ANDs are otherwise common in normal logic.
                succs = [str(s) for s in G.successors(gate_id)]
                for s in succs:
                    stype = (_feat(by_id.get(s, {})).get("gate_type") or "").upper()
                    if stype in ("XOR", "XNOR"):
                        suspects.setdefault(
                            gate_id,
                            f"rarely-satisfied {gtype} (toggle {toggle:.4f}, "
                            f"fan-in {fan_in}) whose only load is {stype} {s} "
                            f"-- the shape of a trigger driving a payload",
                        )

    gates = sorted(suspects)
    reasons = [f"{g}: {suspects[g]}" for g in gates]
    return gates, reasons
