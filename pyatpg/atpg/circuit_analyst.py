"""Interpretive analysis of a circuit, separate from ATPG measurement.

This layer describes *structure*: architecture, notable regions, and why a
given gate or net might be hard to test. It never reports fault coverage or
any other ATPG metric -- those come from Atalanta alone. Every gate id it
emits is validated against the parsed circuit, so a model that invents a
signal name cannot put that name in front of the user.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

try:  # pragma: no cover - exercised only where the SDK is installed
    from google import genai
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None
    genai_types = None

#: Per-request timeout (ms) for the Gemini SDK's HTTP client. See the same
#: constant in pyatpg.atpg.targeting for why this must be set explicitly.
_LLM_TIMEOUT_MS = 20_000

#: How many gate profiles to include as evidence in the prompt.
_PROFILE_BUDGET = 120

#: How many gates each structural observation lists.
_STRUCTURAL_TOP_N = 8


@dataclass
class Observation:
    """One statement about the circuit, tied to concrete gates."""

    title: str
    detail: str
    gates: list[str] = field(default_factory=list)
    confidence: str | None = None


@dataclass
class SuspiciousRegion:
    """A group of gates the analysis considers structurally interesting."""

    name: str
    gates: list[str] = field(default_factory=list)
    rationale: str = ""
    confidence: str | None = None


@dataclass
class CircuitAnalysis:
    """Result of the interpretive layer.

    ``source`` records how this was produced so the UI never presents
    deterministic structural metrics as model reasoning:
    ``llm`` (a model wrote it), ``structural`` (computed from graph metrics,
    no model configured), or ``unavailable``.
    """

    source: str = "unavailable"
    model: str | None = None
    architecture_summary: str = ""
    observations: list[Observation] = field(default_factory=list)
    suspicious_regions: list[SuspiciousRegion] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None

    def referenced_gates(self) -> list[str]:
        gates: list[str] = []
        for observation in self.observations:
            gates.extend(observation.gates)
        for region in self.suspicious_regions:
            gates.extend(region.gates)
        seen: set[str] = set()
        return [g for g in gates if not (g in seen or seen.add(g))]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CircuitAnalyst:
    """Produces a :class:`CircuitAnalysis` for a parsed circuit."""

    def __init__(self, api_key: str | None = None, model: str = "gemini-flash-latest"):
        self.api_key = api_key if (api_key and genai is not None) else None
        self.model = model

    def analyze(
        self,
        circuit_source: str,
        profiles: list[dict[str, Any]],
        stats: dict[str, Any],
        circuit_name: str,
    ) -> CircuitAnalysis:
        if self.api_key:
            try:
                return self._analyze_with_llm(circuit_source, profiles, stats, circuit_name)
            except Exception as exc:  # noqa: BLE001 - degrade to structural
                analysis = self._analyze_structurally(profiles, stats, circuit_name)
                analysis.reasoning.insert(
                    0,
                    f"Model analysis unavailable ({exc}); showing computed structural "
                    "analysis instead.",
                )
                return analysis
        return self._analyze_structurally(profiles, stats, circuit_name)

    # ------------------------------------------------------------------
    # Structural fallback: real computed metrics, no model involved.
    # ------------------------------------------------------------------

    def _analyze_structurally(
        self, profiles: list[dict[str, Any]], stats: dict[str, Any], circuit_name: str
    ) -> CircuitAnalysis:
        analysis = CircuitAnalysis(
            source="structural",
            architecture_summary=(
                f"{circuit_name}: {stats.get('gates', 0)} gates across "
                f"{stats.get('max_level', 0)} logic levels, "
                f"{stats.get('primary_inputs', 0)} primary inputs and "
                f"{stats.get('primary_outputs', 0)} primary outputs. "
                f"Gate mix: {stats.get('gate_types', {})}."
            ),
        )

        gates_only = [p for p in profiles if p.get("features", {}).get("fan_in", 0) > 0]
        if not gates_only:
            analysis.unavailable_reason = "No internal gates were available to analyse."
            return analysis

        def top(key: str, reverse: bool) -> list[dict[str, Any]]:
            return sorted(
                gates_only,
                key=lambda p: p["features"].get(key, 0),
                reverse=reverse,
            )[:_STRUCTURAL_TOP_N]

        hard_to_observe = top("co", reverse=True)
        analysis.observations.append(
            Observation(
                title="Hardest-to-observe logic",
                detail=(
                    "Highest SCOAP observability cost in the circuit: faults here need "
                    "the longest sensitised path to reach a primary output, so they are "
                    "typically the last to be detected."
                ),
                gates=[p["gate_id"] for p in hard_to_observe],
            )
        )

        rarely_toggling = sorted(
            gates_only, key=lambda p: p["features"].get("toggle_rate", 1.0)
        )[:_STRUCTURAL_TOP_N]
        analysis.observations.append(
            Observation(
                title="Rarely-toggling nets",
                detail=(
                    "Lowest switching activity under random simulation. Low toggle rate "
                    "means random patterns rarely exercise these nets, so they depend on "
                    "deterministic pattern generation."
                ),
                gates=[p["gate_id"] for p in rarely_toggling],
            )
        )

        high_fanout = top("fan_out", reverse=True)
        analysis.observations.append(
            Observation(
                title="High fan-out hubs",
                detail=(
                    "These nets drive the most downstream logic, so they sit on many "
                    "reconvergent paths and a fault here can be masked along some of them."
                ),
                gates=[p["gate_id"] for p in high_fanout],
            )
        )

        analysis.suspicious_regions.append(
            SuspiciousRegion(
                name="Low-activity, low-observability cluster",
                gates=[
                    p["gate_id"]
                    for p in gates_only
                    if p["features"].get("toggle_rate", 1.0) < 0.1
                    and p["features"].get("co", 0) > 5
                ][:_STRUCTURAL_TOP_N],
                rationale=(
                    "Gates that both switch rarely and are expensive to observe. This "
                    "combination is what makes logic hard to test; it is a testability "
                    "observation, not evidence of a defect."
                ),
            )
        )

        analysis.reasoning.append(
            "Computed directly from SCOAP controllability/observability and simulated "
            "toggle rates. No language model was used for this analysis."
        )
        return analysis

    # ------------------------------------------------------------------
    # LLM analysis
    # ------------------------------------------------------------------

    def _analyze_with_llm(
        self,
        circuit_source: str,
        profiles: list[dict[str, Any]],
        stats: dict[str, Any],
        circuit_name: str,
    ) -> CircuitAnalysis:
        http_options = (
            genai_types.HttpOptions(timeout=_LLM_TIMEOUT_MS) if genai_types else None
        )
        client = genai.Client(api_key=self.api_key, http_options=http_options)

        ranked = sorted(
            profiles,
            key=lambda p: (
                p["features"].get("toggle_rate", 1.0),
                -p["features"].get("co", 0),
            ),
        )
        profile_block = "\n".join(p["profile"] for p in ranked[:_PROFILE_BUDGET])
        source_excerpt = circuit_source[:8000]

        prompt = f"""You are a design-for-test engineer reviewing a combinational circuit
before automatic test pattern generation.

Circuit: {circuit_name}
Structure: {stats.get('gates', 0)} gates, {stats.get('max_level', 0)} logic levels,
{stats.get('primary_inputs', 0)} primary inputs, {stats.get('primary_outputs', 0)} primary outputs.
Gate mix: {stats.get('gate_types', {})}
Average fan-in {stats.get('avg_fan_in')}, average fan-out {stats.get('avg_fan_out')}.

Describe what this circuit looks like structurally and which parts are likely to be
hard to test, and why. Ground every point in the gate data below.

Rules:
- Only reference gate ids that appear in the gate profiles.
- Do not state or estimate fault coverage, pattern counts, or any ATPG result.
  Those are measured separately by the ATPG tool.
- Describe testability and structure, not defects. You have no evidence of a defect.
- Set confidence to "high", "medium", or "low" based on how strongly the data supports
  each point.

Circuit source (may be truncated):
{source_excerpt}

Gate profiles (rarest-toggling first):
{profile_block}
"""

        config = None
        if genai_types is not None:
            config = genai_types.GenerateContentConfig(response_mime_type="application/json")

        response = client.models.generate_content(
            model=self.model,
            contents=prompt + "\n" + _JSON_CONTRACT,
            config=config,
        )
        payload = _load_json(response.text or "")
        known = {str(p["gate_id"]) for p in profiles}
        return self._build_from_payload(payload, known)

    def _build_from_payload(
        self, payload: dict[str, Any], known_gates: set[str]
    ) -> CircuitAnalysis:
        analysis = CircuitAnalysis(source="llm", model=self.model)
        analysis.architecture_summary = str(payload.get("architecture_summary", "")).strip()

        dropped = 0

        def keep_known(raw: Any) -> list[str]:
            nonlocal dropped
            kept: list[str] = []
            for gate in raw if isinstance(raw, list) else []:
                clean = str(gate).strip()
                if clean in known_gates:
                    if clean not in kept:
                        kept.append(clean)
                else:
                    dropped += 1
            return kept

        for item in payload.get("observations", []) or []:
            if not isinstance(item, dict):
                continue
            analysis.observations.append(
                Observation(
                    title=str(item.get("title", "")).strip() or "Observation",
                    detail=str(item.get("detail", "")).strip(),
                    gates=keep_known(item.get("gates")),
                    confidence=_clean_confidence(item.get("confidence")),
                )
            )

        for item in payload.get("suspicious_regions", []) or []:
            if not isinstance(item, dict):
                continue
            analysis.suspicious_regions.append(
                SuspiciousRegion(
                    name=str(item.get("name", "")).strip() or "Region",
                    gates=keep_known(item.get("gates")),
                    rationale=str(item.get("rationale", "")).strip(),
                    confidence=_clean_confidence(item.get("confidence")),
                )
            )

        reasoning = payload.get("reasoning")
        if isinstance(reasoning, list):
            analysis.reasoning = [str(r).strip() for r in reasoning if str(r).strip()]
        elif isinstance(reasoning, str) and reasoning.strip():
            analysis.reasoning = [reasoning.strip()]

        if dropped:
            analysis.reasoning.append(
                f"{dropped} gate reference(s) produced by the model did not exist in "
                "the parsed circuit and were removed."
            )
        return analysis


_JSON_CONTRACT = """Respond with JSON only, in exactly this shape:
{
  "architecture_summary": "string",
  "observations": [
    {"title": "string", "detail": "string", "gates": ["gate_id"], "confidence": "high|medium|low"}
  ],
  "suspicious_regions": [
    {"name": "string", "gates": ["gate_id"], "rationale": "string", "confidence": "high|medium|low"}
  ],
  "reasoning": ["string"]
}"""


def _clean_confidence(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"high", "medium", "low"} else None


def _load_json(text: str) -> dict[str, Any]:
    """Parse a model response as JSON, tolerating markdown fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model did not return valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Model returned JSON that was not an object.")
    return data
