"""Selection of which gates the targeted ATPG run should focus on.

This decides the contents of Atalanta's -f fault list. It is a *selection*
step only: nothing here produces ATPG metrics, and nothing here is treated
as evidence about a gate. Atalanta remains the sole source of truth for
whether a fault is detectable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover - exercised only where the SDK is installed
    from google import genai
except ImportError:  # pragma: no cover
    genai = None

#: Transient Gemini failures worth retrying.
_RETRYABLE_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL")

#: Cap on how many gates the targeted fault list may contain, used verbatim
#: when a caller passes an explicit ``max_gates``. Automatic selection (the
#: default) instead scales with circuit size -- see ``_scaled_max_gates`` --
#: because a fixed cap becomes a shrinking fraction of the circuit's faults
#: as the circuit grows, dragging the targeted run's own fault coverage down
#: for reasons unrelated to how well the gates were chosen.
MAX_TARGET_GATES = 30

#: Share of profiled gates the automatic cap targets.
_TARGET_GATE_FRACTION = 0.25

#: Floor and ceiling around that fraction, so tiny circuits still get a
#: meaningful fault list and huge circuits don't turn a "targeted" run into
#: a full one.
_MIN_TARGET_GATES = 15
_MAX_TARGET_GATES_CEILING = 200

#: Structural thresholds for the offline selector. These mirror the
#: Trust-Hub style heuristics: rarely-toggling, multi-input, hard-to-observe.
_MAX_TOGGLE_RATE = 0.1
_MIN_FAN_IN = 2
_MIN_OBSERVABILITY = 5


@dataclass
class TargetSelection:
    """The gates chosen for targeting, and how they were chosen."""

    gates: list[str] = field(default_factory=list)
    strategy: str = "structural-heuristic"
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)


class FaultTargetSelector:
    """Chooses gates for the targeted run, via LLM when available.

    Falls back to a deterministic structural heuristic when no API key is
    configured, so the targeted mode works without credentials.
    """

    def __init__(self, api_key: str | None = None, max_gates: int | None = None):
        #: None means "scale with circuit size" (the default); an explicit
        #: value pins the cap regardless of how many gates are profiled.
        self.max_gates = max_gates
        self.api_key = api_key
        if api_key and genai is None:
            self.api_key = None

    def _effective_max_gates(self, profiles: list[dict[str, Any]]) -> int:
        if self.max_gates is not None:
            return self.max_gates
        scaled = round(len(profiles) * _TARGET_GATE_FRACTION)
        return max(_MIN_TARGET_GATES, min(_MAX_TARGET_GATES_CEILING, scaled))

    def select(self, profiles: list[dict[str, Any]], summary: str = "") -> TargetSelection:
        max_gates = self._effective_max_gates(profiles)
        if self.api_key:
            try:
                gates = self._select_with_llm(profiles, summary, max_gates)
                valid = self._keep_known_gates(gates, profiles)
                selection = TargetSelection(
                    gates=valid[:max_gates],
                    strategy="llm",
                    used_llm=True,
                )
                if len(valid) < len(gates):
                    selection.notes.append(
                        f"{len(gates) - len(valid)} gate id(s) returned by the model "
                        "were not present in the circuit and were discarded."
                    )
                if not valid:
                    selection.notes.append(
                        "Model returned no usable gate ids; fell back to the "
                        "structural heuristic."
                    )
                    return self._structural_selection(profiles, max_gates, selection.notes)
                return selection
            except Exception as exc:  # noqa: BLE001 - degrade, don't fail the run
                return self._structural_selection(
                    profiles,
                    max_gates,
                    [f"LLM targeting unavailable ({exc}); used the structural heuristic."],
                )
        return self._structural_selection(profiles, max_gates, [])

    def _structural_selection(
        self, profiles: list[dict[str, Any]], max_gates: int, notes: list[str]
    ) -> TargetSelection:
        ranked = [
            p
            for p in profiles
            if self._is_structurally_interesting(p.get("features", {}))
        ]
        # Rarest-toggling and least-observable first, so a truncated list
        # keeps the most interesting candidates.
        ranked.sort(
            key=lambda p: (
                p["features"].get("toggle_rate", 1.0),
                -p["features"].get("co", 0),
            )
        )
        return TargetSelection(
            gates=[p["gate_id"] for p in ranked[:max_gates]],
            strategy="structural-heuristic",
            used_llm=False,
            notes=notes,
        )

    @staticmethod
    def _is_structurally_interesting(features: dict[str, Any]) -> bool:
        try:
            return (
                features["toggle_rate"] < _MAX_TOGGLE_RATE
                and features["fan_in"] >= _MIN_FAN_IN
                and features["co"] > _MIN_OBSERVABILITY
            )
        except (KeyError, TypeError):
            return False

    @staticmethod
    def _keep_known_gates(gates: list[str], profiles: list[dict[str, Any]]) -> list[str]:
        """Drop hallucinated ids: a gate must exist in the parsed circuit.

        Without this an invented id reaches Atalanta's -f list and the run
        fails or silently targets nothing.
        """
        known = {str(p["gate_id"]) for p in profiles}
        seen: set[str] = set()
        kept: list[str] = []
        for gate in gates:
            clean = str(gate).strip()
            if clean in known and clean not in seen:
                seen.add(clean)
                kept.append(clean)
        return kept

    def _select_with_llm(
        self, profiles: list[dict[str, Any]], summary: str, max_gates: int
    ) -> list[str]:
        client = genai.Client(api_key=self.api_key)

        ordered = sorted(
            profiles,
            key=lambda p: (
                p["features"].get("toggle_rate", 1.0),
                -p["features"].get("co", 0),
            ),
        )
        profile_block = "\n".join(p["profile"] for p in ordered[:200])

        prompt = f"""{summary}

You are assisting with test-pattern generation for a combinational circuit.
Select the gates whose stuck-at faults are most worth targeting explicitly:
rarely-toggling, high fan-in, or hard to observe (high SCOAP CO).

Return at most {max_gates} gate ids, comma-separated, nothing else.

Gate profiles:
{profile_block}
"""

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model="gemini-flash-latest",
                    contents=prompt,
                )
                text = (response.text or "").replace("\n", " ")
                return [g.strip() for g in text.split(",") if g.strip()]
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if not any(m in str(exc) for m in _RETRYABLE_MARKERS) or attempt == 2:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError(f"Gemini request failed: {last_error}")
