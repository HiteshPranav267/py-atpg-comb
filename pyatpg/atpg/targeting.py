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

from pyatpg.llm.token_tracker import TokenTracker
from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError, provider_from_request

from .llm_context import (
    COMPACT_LEGEND,
    REQUEST_TOKEN_BUDGET_ENV_VAR,
    LLMResultCache,
    build_candidate_pool,
    cache_enabled,
    compact_profile_block,
    context_gate_limit,
    estimate_prompt_tokens,
    reply_budget,
    request_token_budget,
)

#: Reply cost of one "gate_id,confidence" line, the only thing this stage
#: asks the model to return.
_TOKENS_PER_TARGET_LINE = 12

#: Never shrink the candidate pool below this, whatever the token budget
#: says: past some point the pool stops containing the answer at all, and
#: failing loudly beats a selection made from too little evidence.
_MIN_CONTEXT_POOL = 40

#: Stage name this selector's calls are logged under in the TokenTracker.
TOKEN_STAGE = "targeting"

#: Cap on how many gates the targeted fault list may contain, used verbatim
#: when a caller passes an explicit ``max_gates``. Automatic selection (the
#: default) instead scales with circuit size -- see ``_scaled_max_gates`` --
#: because a fixed cap becomes a shrinking fraction of the circuit's faults
#: as the circuit grows, dragging the targeted run's own fault coverage down
#: for reasons unrelated to how well the gates were chosen.
MAX_TARGET_GATES = 30

#: Share of profiled gates the automatic cap targets. A few points above the
#: bare minimum needed for a "targeted" run: on small fault lists, one or two
#: hard-to-detect/redundant faults swing targeted_coverage by several points,
#: so a little slack keeps that percentage from reading worse than the
#: non-targeted run purely because the list was too thin to absorb them.
_TARGET_GATE_FRACTION = 0.3

#: Floor and ceiling around that fraction, so tiny circuits still get a
#: meaningful fault list and huge circuits don't turn a "targeted" run into
#: a full one.
_MIN_TARGET_GATES = 20
_MAX_TARGET_GATES_CEILING = 200

#: Structural thresholds for the offline selector. These mirror the
#: Trust-Hub style heuristics: rarely-toggling, multi-input, hard-to-observe.
_MAX_TOGGLE_RATE = 0.1
_MIN_FAN_IN = 2
_MIN_OBSERVABILITY = 5

#: Minimum confidence for a gate to enter the targeted fault list. Gates the
#: model scores below this are dropped rather than tested.
_MIN_CONFIDENCE = 0.3

#: Worked examples of what inserted logic looks like in feature terms, given
#: to the model as support-set "shots". Deliberately written as feature
#: patterns rather than named gates: the point is to teach the shape, so it
#: transfers to a netlist whose signals are named arbitrarily. Values are
#: illustrative of the general profile, not copied from any test circuit.
_FEW_SHOT_EXEMPLARS = """### Worked examples

Example A -- a trigger. A 3-input AND sitting among 2-input NANDs, toggle
rate 0.02 against a circuit median near 0.3, fan-out of exactly 1, CO higher
than its immediate neighbours. Suspicious not because 0.02 is the lowest in
the circuit (it usually is not) but because the gate is rare AND narrow AND
feeds a single load -- a conjunction built to fire almost never.

Example B -- a payload. A 2-input XOR whose two inputs have wildly different
toggle rates: one near the circuit median, one an order of magnitude lower.
On a normal data path both inputs of an XOR swing at comparable rates. This
asymmetry is the signature of logic spliced into an existing path, where one
side is the original signal and the other is a trigger.

Example C -- NOT suspicious. A gate with the circuit's single lowest toggle
rate, high CC0/CC1, sitting in a deep arithmetic cone with several fan-outs
and neighbours of similar rarity. Extreme testability numbers alone are
ordinary in benign logic -- deep carry chains and comparators look like this.
Rarity matters when it is anomalous for its neighbourhood, not when it is
simply the tail of the distribution."""


@dataclass
class TargetSelection:
    """The gates chosen for targeting, and how they were chosen."""

    gates: list[str] = field(default_factory=list)
    strategy: str = "structural-heuristic"
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)
    #: Gates flagged by the deterministic trojan-shape detector, and why.
    #: These are force-included in ``gates`` regardless of what the model
    #: returned, so a model miss cannot drop them from the fault list.
    structural_suspects: list[str] = field(default_factory=list)
    suspect_reasons: list[str] = field(default_factory=list)
    #: True when the model's answer came from the on-disk cache of an
    #: earlier identical run, so this selection cost no tokens.
    cached: bool = False
    #: What the model was shown: how many gate profiles out of how many, and
    #: the cap that decided it. Empty when no model was consulted.
    context: dict[str, Any] = field(default_factory=dict)


class FaultTargetSelector:
    """Chooses gates for the targeted run, via LLM when available.

    Falls back to a deterministic structural heuristic when no API key is
    configured, so the targeted mode works without credentials.
    """

    def __init__(
        self,
        api_key: str | None = None,
        max_gates: int | None = None,
        provider: LLMProvider | None = None,
        tracker: TokenTracker | None = None,
        context_gate_limit: int | None = None,
        cache: LLMResultCache | None = None,
    ):
        #: None means "scale with circuit size" (the default); an explicit
        #: value pins the cap regardless of how many gates are profiled.
        self.max_gates = max_gates
        #: Every model call is logged here; a fresh tracker is used when the
        #: caller does not share one.
        self.tracker = tracker or TokenTracker()
        #: How many gate profiles the prompt may carry (see llm_context).
        self.context_gate_limit = context_gate_limit
        #: Cache of earlier answers for the same netlist; None means "use
        #: the default on-disk cache when a source_text is supplied".
        self.cache = cache
        #: The model to ask, or None for the structural heuristic. A ready
        #: provider wins; a bare ``api_key`` is resolved against the
        #: environment (provider sniffed from the key's prefix).
        self.provider = provider
        if self.provider is None and api_key:
            try:
                self.provider = provider_from_request(api_key=api_key)
            except (ImportError, ValueError):
                self.provider = None

    def _effective_max_gates(self, profiles: list[dict[str, Any]]) -> int:
        if self.max_gates is not None:
            return self.max_gates
        scaled = round(len(profiles) * _TARGET_GATE_FRACTION)
        return max(_MIN_TARGET_GATES, min(_MAX_TARGET_GATES_CEILING, scaled))

    def select(
        self,
        profiles: list[dict[str, Any]],
        summary: str = "",
        graph: Any = None,
        source_text: str | None = None,
    ) -> TargetSelection:
        """Choose the gates for the targeted fault list.

        ``graph`` enables the deterministic trojan-shape detector; when it is
        provided, anything that detector flags is merged into the result no
        matter which selection path ran, so a model miss (or an LLM outage)
        cannot silently drop inserted logic from the fault list.

        ``source_text`` is the netlist the profiles came from; when given,
        the model's answer is cached against it so an identical re-run
        costs no tokens.
        """
        max_gates = self._effective_max_gates(profiles)
        suspects, reasons = self._find_suspects(profiles, graph)

        if self.provider is not None:
            try:
                gates, context, cached = self._select_with_llm(
                    profiles, summary, max_gates, graph, suspects, source_text
                )
                valid = self._keep_known_gates(gates, profiles)
                selection = TargetSelection(
                    gates=valid[:max_gates],
                    strategy="llm (cached)" if cached else "llm",
                    used_llm=True,
                    cached=cached,
                    context=context,
                )
                selection.notes.extend(context.get("notes", []))
                if cached:
                    selection.notes.append(
                        "Gate selection was reused from the cache of an earlier run of "
                        "this exact netlist through the same model, so this step cost "
                        "no tokens. Set LLM_RESULT_CACHE=0 for a fresh model call."
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
                    return self._merge_suspects(
                        self._structural_selection(profiles, max_gates, selection.notes),
                        suspects,
                        reasons,
                    )
                return self._merge_suspects(selection, suspects, reasons)
            except LLMUnavailableError:
                # The model could not be consulted at all (no credits, rate
                # limited, provider 5xx). The user asked for an LLM-assisted
                # run; quietly handing back the structural heuristic instead
                # would misrepresent what was done, so stop here and say why.
                raise
            except Exception as exc:  # noqa: BLE001 - model ran but its answer was unusable
                return self._merge_suspects(
                    self._structural_selection(
                        profiles,
                        max_gates,
                        [f"LLM targeting unavailable ({exc}); used the structural heuristic."],
                    ),
                    suspects,
                    reasons,
                )
        return self._merge_suspects(
            self._structural_selection(profiles, max_gates, []), suspects, reasons
        )

    @staticmethod
    def _find_suspects(
        profiles: list[dict[str, Any]], graph: Any
    ) -> tuple[list[str], list[str]]:
        if graph is None:
            return [], []
        try:
            from .suspects import find_structural_suspects

            return find_structural_suspects(profiles, graph)
        except Exception:  # noqa: BLE001 - a detector failure must not fail the run
            return [], []

    @staticmethod
    def _merge_suspects(
        selection: TargetSelection, suspects: list[str], reasons: list[str]
    ) -> TargetSelection:
        """Force structurally-suspicious gates into the selection.

        Appended rather than inserted, and exempt from ``max_gates``: the cap
        exists to keep the targeted run small relative to a full one, and a
        handful of flagged gates does not threaten that. Dropping them to
        respect the cap would defeat the point of detecting them.
        """
        selection.structural_suspects = list(suspects)
        selection.suspect_reasons = list(reasons)
        if not suspects:
            return selection

        already = set(selection.gates)
        added = [g for g in suspects if g not in already]
        if added:
            selection.gates = selection.gates + added
            selection.notes.append(
                f"{len(added)} gate(s) matching a trojan-like structural pattern "
                f"were added to the fault list by the deterministic detector "
                f"(not selected by the {'model' if selection.used_llm else 'heuristic'}): "
                + ", ".join(added)
            )
        else:
            selection.notes.append(
                f"The deterministic detector flagged {len(suspects)} gate(s) as "
                "trojan-shaped; all were already in the selection."
            )
        return selection

    def _structural_selection(
        self, profiles: list[dict[str, Any]], max_gates: int, notes: list[str]
    ) -> TargetSelection:
        def rank_key(p):
            return (
                p["features"].get("toggle_rate", 1.0),
                -p["features"].get("co", 0),
            )

        ranked = [
            p
            for p in profiles
            if self._is_structurally_interesting(p.get("features", {}))
        ]
        # Rarest-toggling and least-observable first, so a truncated list
        # keeps the most interesting candidates.
        ranked.sort(key=rank_key)

        # All three thresholds must hold at once, so on a circuit whose logic
        # happens not to satisfy them the filter can return almost nothing --
        # a targeted run of one or two faults, which reports a meaningless
        # 100% coverage and tests essentially none of the circuit. Top up from
        # the same ranking so the fault list is always a usable size.
        floor = min(_MIN_TARGET_GATES, max_gates, len(profiles))
        if len(ranked) < floor:
            qualified = len(ranked)
            chosen = {id(p) for p in ranked}
            for p in sorted(profiles, key=rank_key):
                if len(ranked) >= floor:
                    break
                if id(p) not in chosen:
                    ranked.append(p)
                    chosen.add(id(p))
            notes = notes + [
                f"Only {qualified} gate(s) met all structural thresholds; topped up "
                f"to {len(ranked)} from the same rarity ranking so the targeted run "
                f"covers a meaningful share of the circuit."
            ]

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
    def _parse_scored_response(
        text: str, alias_to_gate: dict[str, str]
    ) -> list[str]:
        """Read "alias,confidence" lines back into real gate names.

        Tolerates the model answering in the older bare comma-separated form
        (no scores) -- those gates are kept, since a missing score is not the
        same as a low one. Aliases the model invents resolve to nothing and
        are dropped here, before ``_keep_known_gates`` ever sees them.
        """
        scored: list[tuple[float, str]] = []
        seen: set[str] = set()

        for raw_line in text.replace(";", "\n").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if not parts:
                continue

            # "alias,confidence" -- the requested form.
            if len(parts) == 2 and parts[0] in alias_to_gate:
                try:
                    confidence = float(parts[1])
                except ValueError:
                    confidence = 1.0
                if parts[0] not in seen and confidence >= _MIN_CONFIDENCE:
                    seen.add(parts[0])
                    scored.append((confidence, parts[0]))
                continue

            # Fall back to bare aliases, possibly several per line.
            for token in parts:
                if token in alias_to_gate and token not in seen:
                    seen.add(token)
                    scored.append((1.0, token))

        scored.sort(key=lambda pair: -pair[0])
        return [alias_to_gate[alias] for _, alias in scored]

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

    def _compose_prompt(
        self,
        profiles: list[dict[str, Any]],
        summary: str,
        max_gates: int,
        graph: Any,
        suspects: list[str] | None,
        limit: int,
    ) -> tuple[str, list[dict[str, Any]], list[str], dict[str, str]]:
        """Build the pool and the prompt carrying it, for one pool ``limit``.

        Separate from :meth:`_select_with_llm` so the caller can rebuild at
        a smaller limit when the result would not fit the request budget.
        Returns (prompt, pool, pool notes, alias -> real gate id).
        """
        pool, pool_notes = build_candidate_pool(profiles, graph, limit, suspects)

        # Anonymise gate names before they reach the model.
        #
        # The framework's premise is that gate-level structural features are
        # obfuscation-invariant -- that detection survives an adversary
        # renaming every signal, which is what defeats RTL/source-level
        # detectors. Sending the real names breaks that premise in the worst
        # possible way here, because inserted logic in the generated corpus is
        # named ht_trigger_out_N / HT_PAYLOAD_XOR and the ground truth is
        # literally "does the name contain HT_". A model could score perfectly
        # by string-matching, and any measured result would be evidence about
        # the naming convention rather than about structural reasoning.
        #
        # Substituting opaque ids makes the measurement mean what it claims to
        # mean, and makes the run identical to one on a renamed netlist.
        alias_to_gate: dict[str, str] = {}
        rows: list[tuple[str, dict[str, Any]]] = []
        for index, p in enumerate(pool):
            alias = f"g{index}"
            alias_to_gate[alias] = str(p["gate_id"])
            rows.append((alias, p.get("features", {}) or {}))
        profile_block = compact_profile_block(rows)

        floor = min(_MIN_TARGET_GATES, len(pool))
        prompt = f"""{summary}

You are assisting with test-pattern generation for a combinational circuit,
with the specific goal of catching logic that may have been maliciously
inserted (a hardware trojan) as well as ordinary hard-to-test faults.

Select the gates whose stuck-at faults are most worth targeting explicitly.
Weigh two different kinds of evidence:

1. Testability. Rarely-toggling, high fan-in, or hard to observe (high SCOAP
   CO). These faults are the ones random patterns are least likely to catch.
   Weigh reconvergent fanout heavily (high reconv): where a stem's branches
   rejoin, they constrain each other, which is where faults become redundant
   or so hard that deterministic search aborts on them.

2. Structural anomaly. Inserted logic often does NOT stand out on testability
   alone -- it is chosen to blend in. Look for gates whose relationship to
   their neighbours is odd: a gate whose toggle rate is far out of line with
   the gates around it, an XOR/XNOR sitting on an otherwise plain path, a
   narrow-fan-out gate feeding a single unusual load, or a small cluster of
   gates whose profile differs from the surrounding logic.

{_FEW_SHOT_EXEMPLARS}

Do not return only the single most extreme gate. One gate being a clear
outlier on toggle rate does not mean the rest are uninteresting -- inserted
logic is usually NOT the most extreme thing in the circuit, so a one-gate
answer will miss it. Return a spread of at least {floor} gates unless the
circuit is smaller than that, covering both kinds of evidence above.

### Output format
Return one line per gate, most suspicious first, as:

    gate_id,confidence

where confidence is between 0.0 and 1.0. Output nothing else -- no prose, no
headers. Return between {floor} and {max_gates} gates.

### Gate profiles
{COMPACT_LEGEND}
Rarest-toggling first.

{profile_block}
"""
        return prompt, pool, pool_notes, alias_to_gate

    def _select_with_llm(
        self,
        profiles: list[dict[str, Any]],
        summary: str,
        max_gates: int,
        graph: Any = None,
        suspects: list[str] | None = None,
        source_text: str | None = None,
    ) -> tuple[list[str], dict[str, Any], bool]:
        """Ask the model, returning (gates, context description, was_cached)."""
        # Decide what the model sees. Everything, for a circuit within the
        # cap; otherwise a pool built from several deterministic views so a
        # gate ranked just below any single cutoff is not invisible (see
        # llm_context for why a plain top-N truncation was not good enough).
        limit = context_gate_limit(self.context_gate_limit)
        # The model must be able to answer with max_gates distinct gates.
        limit = max(limit, max_gates) if limit else 0
        prompt, pool, pool_notes, alias_to_gate = self._compose_prompt(
            profiles, summary, max_gates, graph, suspects, limit
        )

        # A gate *count* is only a rough proxy for what a request costs, and
        # a provider that refuses an oversized one (Groq's free tier caps a
        # single request at 8000 tokens) rejects it before the model runs.
        # So measure what was actually built and, if it will not fit, ask
        # for less -- of both kinds, because the two costs are independent:
        # the pool is what the prompt carries, while max_gates sets how long
        # the answer may be, and a reply bound is reserved against the same
        # budget whether or not the model uses it. On a tight budget,
        # shrinking only the pool cannot help once its floor is the number
        # of gates the answer still has to be able to name.
        budget = request_token_budget()
        effective_max_gates = max_gates
        if limit > 0 and budget > 0:
            pool_limit = limit
            while (
                estimate_prompt_tokens(prompt)
                + reply_budget(effective_max_gates * _TOKENS_PER_TARGET_LINE)
                > budget
            ):
                at_gate_floor = effective_max_gates <= _MIN_TARGET_GATES
                at_pool_floor = len(pool) <= max(effective_max_gates, _MIN_CONTEXT_POOL)
                if at_gate_floor and at_pool_floor:
                    break
                effective_max_gates = max(
                    _MIN_TARGET_GATES, int(effective_max_gates * 0.7)
                )
                pool_limit = max(
                    effective_max_gates, _MIN_CONTEXT_POOL, int(pool_limit * 0.7)
                )
                prompt, pool, pool_notes, alias_to_gate = self._compose_prompt(
                    profiles, summary, effective_max_gates, graph, suspects, pool_limit
                )
            if effective_max_gates != max_gates or len(pool) < limit:
                pool_notes = [
                    *pool_notes,
                    f"Trimmed to {len(pool)} gates of context and at most "
                    f"{effective_max_gates} targeted gates to fit a {budget}-token "
                    f"request budget. Set {REQUEST_TOKEN_BUDGET_ENV_VAR}=0 to "
                    "disable this, or raise it on a provider whose per-request "
                    "limit is larger than Groq's free tier.",
                ]

        context = {
            "gates_sent": len(pool),
            "gates_total": len(profiles),
            "gate_limit": limit,
            "notes": pool_notes,
        }
        # An identical netlist through the same model with the same pool
        # settings would get the same answer at full price.
        # Keyed on what was actually sent: a budget-trimmed request asks a
        # different question than the full one, and must not read back its
        # answer from the cache (or leave its own answer where the full one
        # would be found).
        cache_params = dict(
            model=self.provider.model_name,
            max_gates=effective_max_gates,
            limit=len(pool),
        )
        cache = self.cache
        if cache is None and source_text is not None and cache_enabled():
            cache = LLMResultCache()
        if cache is not None and source_text is not None:
            hit = cache.get(TOKEN_STAGE, source_text, **cache_params)
            if isinstance(hit, dict) and isinstance(hit.get("response"), str):
                self.tracker.record_cached(TOKEN_STAGE)
                # The alias map is rebuilt from the same deterministic pool,
                # so the cached response resolves to the same gates.
                return (
                    self._parse_scored_response(hit["response"], alias_to_gate),
                    context,
                    True,
                )

        # Retries on transient failures live inside the provider.
        started = time.perf_counter()
        response_text = self.provider.complete(
            prompt,
            max_output_tokens=reply_budget(
                effective_max_gates * _TOKENS_PER_TARGET_LINE
            ),
        ) or ""
        elapsed = time.perf_counter() - started
        self.tracker.record(
            TOKEN_STAGE, prompt, response_text,
            usage=getattr(self.provider, "last_usage", None), duration_s=elapsed,
        )
        if cache is not None and source_text is not None:
            cache.put(TOKEN_STAGE, source_text, {"response": response_text}, **cache_params)
        return self._parse_scored_response(response_text, alias_to_gate), context, False
