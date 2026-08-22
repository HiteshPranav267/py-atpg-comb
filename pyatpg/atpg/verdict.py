"""Final trojan verdict for a combinational run.

Replaces the original fusion rule, which required a gate to be flagged
structurally *and* reported by ATPG as undetected/redundant/untestable
before it counted as a candidate. Measured over the project's ISCAS-85
corpus, that rule fired zero times in 29 runs -- including on the
non-targeted runs, which see every fault in the circuit.

The reason is structural rather than incidental. A combinational trojan of
the shape this framework inserts -- a conjunction feeding an XOR spliced
into an existing path -- is *trivially testable*. ATPG drives the primary
inputs directly and can force whatever internal combination it needs, so it
generates a detecting pattern without difficulty. The trigger's rarity is a
property of normal functional operation, not of deliberate test generation.
The old rule conflated those two, and so could only ever fire on trojans
that were unreachable or redundant -- which a working trojan never is.

What ATPG genuinely contributes is the opposite signal: it *confirms* the
suspect logic is real, reachable, and gives the concrete input pattern that
activates it. That is the useful output for an auditor, and it is what this
module reports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import AtalantaRun

#: Statuses meaning ATPG produced no detecting pattern for a fault.
_NO_PATTERN = {"undetected", "redundant", "aborted", "untestable"}


@dataclass
class SuspectGate:
    """One gate carrying trojan evidence, and what ATPG said about it."""

    gate: str
    confidence: float
    evidence: str = "structural"
    reasons: list[str] = field(default_factory=list)
    atpg_status: str = "detected"
    activating_vectors: list[str] = field(default_factory=list)
    statement: str = ""


@dataclass
class TrojanVerdict:
    """The run's conclusion, with the evidence behind it."""

    #: "trojan_suspected" | "inconclusive" | "no_trojan_indicated"
    verdict: str = "no_trojan_indicated"
    confidence: float = 0.0
    suspects: list[SuspectGate] = field(default_factory=list)
    summary: str = ""
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fault_index(runs: list[AtalantaRun]) -> dict[str, set[str]]:
    """gate -> set of fault statuses ATPG reported for it."""
    index: dict[str, set[str]] = {}
    for run in runs:
        for fault in run.faults:
            index.setdefault(fault.gate, set()).add(fault.status)
    return index


def _vectors_for(runs: list[AtalantaRun], limit: int = 3) -> list[str]:
    """A few generated patterns, as the concrete way to exercise a suspect.

    Atalanta's vector file is per-run, not per-fault, so these are the
    run's patterns rather than a pattern proven to activate one specific
    gate. Labelled as such in the statement rather than overclaimed.
    """
    for run in runs:
        vectors = getattr(run.test_vectors, "vectors", None) or []
        if vectors:
            out = []
            for v in vectors[:limit]:
                inputs = getattr(v, "inputs", None)
                if inputs:
                    out.append(str(inputs))
            if out:
                return out
    return []


def build_verdict(
    structural_suspects: list[str],
    suspect_reasons: list[str],
    runs: list[AtalantaRun],
    targeted_gates: list[str] | None = None,
) -> TrojanVerdict:
    """Combine structural evidence with what ATPG measured.

    Args:
        structural_suspects: gates the deterministic shape detector flagged.
        suspect_reasons: parallel human-readable reasons, "gate: why".
        runs: every Atalanta run performed for this circuit.
        targeted_gates: gates the Phase-3 prioritizer selected, used as
            weaker corroborating evidence only.

    Returns:
        A verdict that can actually be reached: structural evidence is the
        trigger, and ATPG's confirmation that the logic is testable raises
        confidence rather than being required to lower it.
    """
    verdict = TrojanVerdict()
    reason_map: dict[str, list[str]] = {}
    for entry in suspect_reasons:
        gate, _, why = entry.partition(":")
        reason_map.setdefault(gate.strip(), []).append(why.strip() or entry)

    statuses = _fault_index(runs)
    vectors = _vectors_for(runs)
    targeted = set(targeted_gates or [])

    for gate in structural_suspects:
        gate_statuses = statuses.get(gate, set())
        blocked = bool(gate_statuses & _NO_PATTERN)

        if blocked:
            # Suspicious in a different way: the logic exists but ATPG could
            # not produce a pattern for it. Genuinely unusual, and worth
            # surfacing -- but it is weaker evidence of a *working* trojan
            # than logic we can demonstrably activate.
            confidence = 0.6
            status = ", ".join(sorted(gate_statuses))
            statement = (
                f"Gate {gate} matches a trojan-like structural pattern, and ATPG "
                f"reported its fault(s) as {status} -- no activating pattern was "
                f"produced. Either genuinely redundant logic or a trigger condition "
                f"the backtrack limit could not reach."
            )
        else:
            confidence = 0.85
            status = "detected"
            statement = (
                f"Gate {gate} matches a trojan-like structural pattern, and ATPG "
                f"generated a detecting pattern for its fault(s) -- the logic is "
                f"real, reachable and can be exercised on silicon."
            )

        if gate in targeted:
            confidence = min(0.95, confidence + 0.05)

        verdict.suspects.append(
            SuspectGate(
                gate=gate,
                confidence=round(confidence, 2),
                evidence="structural+atpg" if gate in targeted else "structural",
                reasons=reason_map.get(gate, []),
                atpg_status=status,
                activating_vectors=vectors if not blocked else [],
                statement=statement,
            )
        )

    verdict.suspects.sort(key=lambda s: -s.confidence)

    if verdict.suspects:
        verdict.verdict = "trojan_suspected"
        verdict.confidence = verdict.suspects[0].confidence
        verdict.summary = (
            f"{len(verdict.suspects)} gate(s) carry the structural signature of an "
            f"inserted trojan: a rarely-satisfied conjunction feeding a payload "
            f"spliced into an existing signal path. This is a structural finding "
            f"about the netlist's shape, corroborated by what ATPG could generate."
        )
    else:
        verdict.verdict = "no_trojan_indicated"
        verdict.confidence = 0.0
        verdict.summary = (
            "No gate in this netlist matches the structural signature of an "
            "inserted combinational trojan. This is not proof the circuit is "
            "clean -- only that nothing matched the shapes this detector encodes."
        )

    verdict.caveats = [
        "The detector encodes specific structural shapes (rare conjunction driving "
        "a spliced XOR/XNOR payload). A trojan built differently will not match it, "
        "so a clean verdict is an absence of evidence, not evidence of absence.",
        "Detection is based on netlist topology, never on signal names, so the "
        "result is unchanged by renaming every wire in the design.",
        "ATPG confirms a suspect's logic is reachable and supplies patterns that "
        "exercise it; it does not itself decide whether that logic is malicious.",
        "Test vectors listed are the run's generated patterns, not patterns proven "
        "to activate one specific gate.",
    ]
    return verdict
