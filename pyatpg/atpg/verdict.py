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


#: Confidence of a suspect backed only by the XOR-splice shape. Low on
#: purpose: on Trust-Hub s15850 that shape matches the same 63 benign gates
#: in the clean and the infected netlist, so on its own it is a lead, not a
#: finding.
_SHAPE_ONLY_CONFIDENCE = 0.35
#: A verdict of "trojan_suspected" needs a suspect at least this confident.
_SUSPECTED_THRESHOLD = 0.7


def build_verdict(
    structural_suspects: list[str],
    suspect_reasons: list[str],
    runs: list[AtalantaRun],
    targeted_gates: list[str] | None = None,
    trigger_evidence: Any = None,
) -> TrojanVerdict:
    """Combine trigger evidence, structural shape and what ATPG measured.

    Args:
        structural_suspects: gates the XOR-splice shape detector flagged
            (pyatpg.atpg.suspects) -- weak evidence on its own.
        suspect_reasons: parallel human-readable reasons, "gate: why".
        runs: every Atalanta run performed for this circuit.
        targeted_gates: gates the targeted selection chose, used as weak
            corroborating evidence only.
        trigger_evidence: a :class:`pyatpg.atpg.trigger_detector.TriggerEvidence`
            (never-toggling clusters, logic-clocked registers, rare nets the
            complete run never activated). Only this evidence can make the
            verdict "trojan_suspected".
    """
    verdict = TrojanVerdict()
    reason_map: dict[str, list[str]] = {}
    for entry in suspect_reasons:
        gate, _, why = entry.partition(":")
        reason_map.setdefault(gate.strip(), []).append(why.strip() or entry)

    statuses = _fault_index(runs)
    vectors = _vectors_for(runs)
    targeted = set(targeted_gates or [])
    trigger_by_gate = getattr(trigger_evidence, "by_gate", {}) or {}
    shape = set(structural_suspects)

    for gate in sorted(shape | set(trigger_by_gate)):
        gate_statuses = statuses.get(gate, set())
        blocked = bool(gate_statuses & _NO_PATTERN)
        status = ", ".join(sorted(gate_statuses)) if blocked else "detected"
        kinds = [k for k, _ in trigger_by_gate.get(gate, [])]
        reasons = [r for _, r in trigger_by_gate.get(gate, [])] + reason_map.get(gate, [])

        if kinds:
            confidence = trigger_evidence.strength(gate)
            if gate in shape:
                confidence = min(0.97, confidence + 0.05)
            evidence = "+".join(kinds + (["shape"] if gate in shape else []))
            if blocked:
                statement = (
                    f"Gate {gate} is part of a trigger-like structure ({', '.join(kinds)}), "
                    f"and ATPG proved/left its fault(s) {status}: the condition it computes "
                    "is never produced by any pattern ATPG found -- hidden or unreachable logic."
                )
            else:
                statement = (
                    f"Gate {gate} is part of a trigger-like structure ({', '.join(kinds)}); "
                    "ATPG reaches it, so the patterns below exercise it."
                )
        else:
            confidence = _SHAPE_ONLY_CONFIDENCE
            evidence = "shape"
            statement = (
                f"Gate {gate} matches the rare-conjunction-into-XOR splice shape. This shape "
                "also occurs in benign logic, so it is a lead to inspect, not a finding."
            )
        if gate in targeted:
            confidence = min(0.97, confidence + 0.03)
            evidence += "+targeted"

        verdict.suspects.append(
            SuspectGate(
                gate=gate,
                confidence=round(confidence, 2),
                evidence=evidence,
                reasons=reasons,
                atpg_status=status,
                activating_vectors=vectors if not blocked else [],
                statement=statement,
            )
        )

    verdict.suspects.sort(key=lambda s: (-s.confidence, s.gate))
    strong = [s for s in verdict.suspects if s.confidence >= _SUSPECTED_THRESHOLD]

    if strong:
        verdict.verdict = "trojan_suspected"
        verdict.confidence = strong[0].confidence
        verdict.summary = (
            f"{len(strong)} gate(s) form trigger-like logic: connected groups of nets that "
            "almost never change value, logic driving a register's clock, or rare conditions "
            "a complete ATPG run never produced. Weaker leads are listed after them."
        )
    elif verdict.suspects:
        verdict.verdict = "inconclusive"
        verdict.confidence = verdict.suspects[0].confidence
        verdict.summary = (
            f"{len(verdict.suspects)} gate(s) match the XOR-splice shape, but nothing "
            "trigger-like was found around them. The shape also appears in clean designs, so "
            "this is not evidence of a Trojan on its own."
        )
    else:
        verdict.verdict = "no_trojan_indicated"
        verdict.confidence = 0.0
        verdict.summary = (
            "No trigger-like structure was found: no connected group of never-toggling "
            "gates, no logic-clocked register, no hidden rare condition. This is not proof "
            "the circuit is clean -- only that nothing matched what the detectors look for."
        )

    verdict.caveats = [
        "Only trigger evidence (never-toggling gate groups, logic-clocked registers, "
        "rare conditions a complete ATPG run never produced) can make the verdict "
        "'Trojan suspected'; the XOR-splice shape alone gives 'inconclusive'.",
        "A Trojan whose trigger is a common condition, or which has no trigger, will "
        "not match; a clean verdict is an absence of evidence, not evidence of absence.",
        "Detection uses netlist structure and simulation, never signal names, so the "
        "result is unchanged by renaming every wire in the design.",
        "Test vectors listed are the run's generated patterns, not patterns proven "
        "to activate one specific gate.",
    ]
    return verdict
