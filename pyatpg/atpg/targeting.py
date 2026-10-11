"""Selection of which gates the targeted ATPG run should focus on.

This decides the contents of Atalanta's -f fault list. It is a *selection*
step only: nothing here produces ATPG metrics, and nothing here is treated
as evidence about a gate. Atalanta remains the sole source of truth for
whether a fault is detectable.

Both modes test the same number of gates. Without a model the list is the
structural heuristic's top picks. With one, the heuristic's top picks stay
(the floor) and the model fills the rest of the list with the gates it
scores highest among those it was shown; slots it leaves empty go to the
heuristic's next picks. An LLM run can therefore lose at most the
heuristic's last few picks, and the Trojan gates only it found are exactly
what the model added. Before this, the model's picks replaced the
heuristic's outright: it reviews ~300 of a large circuit's gates, the
heuristic ranks all of them, and on s38417-T200 recall fell from 0.71
without the model to 0.36 with it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pyatpg.llm.token_tracker import TokenTracker
from pyatpg.ranking.providers import LLMProvider, LLMUnavailableError, provider_from_request

from .llm_context import (
    llm_batches,
    COMPACT_LEGEND,
    REQUEST_TOKEN_BUDGET_ENV_VAR,
    LLMResultCache,
    build_candidate_pool,
    cache_enabled,
    compact_profile_block,
    context_gate_limit,
    estimate_prompt_tokens,
    rarity_key,
    reply_budget,
    request_token_budget,
)

#: Reply cost of one "gate_id,score" line; the model scores every gate it
#: is shown.
_TOKENS_PER_SCORE_LINE = 8

#: Never shrink a request below this many gates, whatever the token budget
#: says: past some point a batch stops containing the answer at all.
_MIN_CONTEXT_POOL = 40

#: Stage name this selector's calls are logged under in the TokenTracker.
TOKEN_STAGE = "targeting"

#: Cap on how many gates the targeted fault list may contain, used verbatim
#: when a caller passes an explicit ``max_gates``. Automatic selection (the
#: default) instead scales with circuit size -- see ``_effective_max_gates``
#: -- because a fixed cap becomes a shrinking fraction of the circuit's
#: faults as the circuit grows, dragging the targeted run's own fault
#: coverage down for reasons unrelated to how well the gates were chosen.
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

#: Lowest score (0-100) at which a gate the model scored can take one of
#: its slots; below it the slot goes to the heuristic's next pick.
_MIN_SCORE = 30.0

#: Share of the list the model may fill when the ``llm_share`` setting
#: cannot be read.
_DEFAULT_LLM_SHARE = 0.5

#: A suspicious region from the circuit analysis is offered to the fault
#: list only if it names at most this many gates: the analyst often returns
#: one "region" spanning its whole 120-gate input, which says nothing.
_MAX_ANALYSIS_REGION = 25

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

Example C -- NOT suspicious on its own. A single gate with a very low toggle
rate, high CC0/CC1, sitting in a deep arithmetic cone with several fan-outs.
Isolated extreme testability numbers are ordinary in benign logic -- deep
carry chains and comparators look like this.

Example D -- a sequential trigger (Trust-Hub style). A tree of AND/NOR gates,
every one with toggle_rate 0.0000, wired into each other and converging on
one net that clocks a register (mark trig / clk), and the gates that register
drives (mark pay). Unlike Example C this is a connected GROUP of
never-toggling gates feeding a clock or select line: the trigger is built
from many rare conditions precisely so it never fires in normal use. Score
every gate of such a group high, and the payload gates it controls."""


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
    #: What the model was shown and what it said: how many gates out of how
    #: many, the pool itself and its score per gate, and how the list was
    #: assembled from them. Empty when no model was consulted.
    context: dict[str, Any] = field(default_factory=dict)


def analysis_region_gates(analysis: Any) -> list[str]:
    """Gates of the model's own small suspicious regions (circuit analysis).

    The two model stages see different slices of a large circuit (the
    analyst the 120 rarest gates, the selector a mixed pool), so each can
    name Trojan gates the other never saw: on s38417-T200 the analyst
    flagged Tj_OUT5678/7/8 and the selection did not. They are passed to
    :meth:`FaultTargetSelector.select` as ``hints`` and take the model's
    slots first; the no-LLM baseline never gets them.
    """
    gates: list[str] = []
    for region in getattr(analysis, "suspicious_regions", None) or []:
        if region.gates and len(region.gates) <= _MAX_ANALYSIS_REGION:
            gates.extend(region.gates)
    return list(dict.fromkeys(gates))


def _llm_share() -> float:
    try:
        from pyatpg.core import settings

        return min(1.0, max(0.0, float(settings.get("llm_share"))))
    except Exception:  # noqa: BLE001 - never fail a run over this setting
        return _DEFAULT_LLM_SHARE


def _target_gates_setting() -> int:
    try:
        from pyatpg.core import settings

        return max(0, int(settings.get("target_gates")))
    except Exception:  # noqa: BLE001 - never fail a run over this setting
        return 0


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
        #: None means "scale with circuit size" (the default, unless the
        #: target_gates setting fixes it); an explicit value pins the cap.
        self.max_gates = max_gates
        #: Every model call is logged here; a fresh tracker is used when the
        #: caller does not share one.
        self.tracker = tracker or TokenTracker()
        #: How many gate profiles one request may carry (see llm_context).
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

    def _fixed_budget(self) -> int | None:
        """An exact list size (explicit or the target_gates setting), else None."""
        if self.max_gates is not None:
            return self.max_gates
        return _target_gates_setting() or None

    def _effective_max_gates(self, profiles: list[dict[str, Any]]) -> int:
        fixed = self._fixed_budget()
        if fixed is not None:
            return fixed
        scaled = round(len(profiles) * _TARGET_GATE_FRACTION)
        return max(_MIN_TARGET_GATES, min(_MAX_TARGET_GATES_CEILING, scaled))

    def select(
        self,
        profiles: list[dict[str, Any]],
        summary: str = "",
        graph: Any = None,
        source_text: str | None = None,
        extra_suspects: list[tuple[str, str]] | None = None,
        use_detector: bool = True,
        force_shape: bool = True,
        hints: list[str] | None = None,
    ) -> TargetSelection:
        """Choose the gates for the targeted fault list.

        ``graph`` enables the deterministic trojan-shape detector; when it is
        provided, anything that detector flags is merged into the result no
        matter which selection path ran, so a model miss (or an LLM outage)
        cannot silently drop inserted logic from the fault list.

        ``source_text`` is the netlist the profiles came from; when given,
        the model's scores are cached against it so an identical re-run
        costs no tokens.

        ``use_detector=False`` leaves both detectors out entirely: nothing is
        forced into the pool or the list -- the setting for measuring what
        the LLM finds by itself.

        ``force_shape=False`` keeps the XOR-shape matches out of the fault
        list (they are still reported); the service passes it when the
        trigger detector found something strong.

        ``hints`` are gates the model itself named elsewhere (its circuit
        analysis); they take its slots before its scored picks.
        """
        max_gates = self._effective_max_gates(profiles)
        if not use_detector:
            graph, extra_suspects = None, None
        suspects, reasons = self._find_suspects(profiles, graph)
        # Trigger-detector findings (pyatpg.atpg.trigger_detector), merged the
        # same way: force-included in the list. They go first: they outrank
        # the XOR-shape matches.
        known = {str(p["gate_id"]) for p in profiles}
        trigger = list(dict.fromkeys(g for g, _ in extra_suspects or [] if g in known))
        why = {g: w for g, w in extra_suspects or []}
        shape = [g for g in suspects if g not in set(trigger)]
        reasons = [f"{g}: {why[g]}" for g in trigger] + [
            r for r in reasons if r.partition(":")[0].strip() not in set(trigger)
        ]
        suspects = trigger + shape
        # The XOR-shape matches join the fault list only when asked. Where the
        # trigger detector finds a real Trojan they are noise: on s38584-T200
        # 19 of their 134 gates were Trojan gates and on T300 181 of 295, every
        # one also a trigger-detector finding. On this tool's generated
        # combinational Trojans (c432..c2670) they are the detector that works
        # (16 of 18 Trojan gates; the trigger detector finds 1).
        forced = suspects if force_shape else trigger

        base = self._structural_selection(profiles, max_gates, [])
        if self.provider is not None:
            try:
                selection = self._select_with_llm(
                    profiles, summary, base, graph, suspects, forced, source_text, hints or []
                )
                return self._merge_suspects(selection, suspects, reasons, forced)
            except LLMUnavailableError:
                # The model could not be consulted at all (no credits, rate
                # limited, provider 5xx). The user asked for an LLM-assisted
                # run; quietly handing back the structural heuristic instead
                # would misrepresent what was done, so stop here and say why.
                raise
            except Exception as exc:  # noqa: BLE001 - model ran but its answer was unusable
                base.notes.append(f"LLM targeting unavailable ({exc}); used the structural heuristic.")
                return self._merge_suspects(base, suspects, reasons, forced)
        return self._merge_suspects(base, suspects, reasons, forced)

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
        selection: TargetSelection, suspects: list[str], reasons: list[str],
        forced: list[str] | None = None,
    ) -> TargetSelection:
        """Force the ``forced`` suspects (default: all) into the selection.

        Appended rather than inserted, and exempt from ``max_gates``: the cap
        exists to keep the targeted run small relative to a full one, and a
        handful of flagged gates does not threaten that. Dropping them to
        respect the cap would defeat the point of detecting them.
        """
        selection.structural_suspects = list(suspects)
        selection.suspect_reasons = list(reasons)
        if forced is not None:
            suspects = list(forced)
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

    @staticmethod
    def _rank_key(p: dict[str, Any]) -> tuple[float, float]:
        return (p["features"].get("toggle_rate", 1.0), -p["features"].get("co", 0))

    def _heuristic_ranking(self, profiles: list[dict[str, Any]]) -> list[str]:
        """Every gate in the heuristic's order: qualifying gates, then the rest.

        Rarest-toggling and least-observable first within each part, so any
        prefix of it is what the heuristic would pick at that budget.
        """
        qualified = sorted(
            (p for p in profiles if self._is_structurally_interesting(p.get("features", {}))),
            key=self._rank_key,
        )
        taken = {id(p) for p in qualified}
        rest = sorted((p for p in profiles if id(p) not in taken), key=self._rank_key)
        return [str(p["gate_id"]) for p in qualified + rest]

    def _structural_selection(
        self, profiles: list[dict[str, Any]], max_gates: int, notes: list[str]
    ) -> TargetSelection:
        ranking = self._heuristic_ranking(profiles)
        qualified = sum(1 for p in profiles if self._is_structurally_interesting(p.get("features", {})))
        # All three thresholds must hold at once, so on a circuit whose logic
        # happens not to satisfy them the filter can return almost nothing --
        # a targeted run of one or two faults, which reports a meaningless
        # 100% coverage and tests essentially none of the circuit. Top up from
        # the same ranking so the fault list is always a usable size (the
        # whole budget when one is fixed, to compare recall across budgets).
        fixed = self._fixed_budget() is not None
        floor = min(max_gates if fixed else _MIN_TARGET_GATES, max_gates, len(profiles))
        size = min(max_gates, max(qualified, floor))
        if qualified < floor:
            notes = notes + [
                f"Only {qualified} gate(s) met all structural thresholds; topped up "
                f"to {size} from the same rarity ranking so the targeted run "
                f"covers a meaningful share of the circuit."
            ]
        return TargetSelection(
            gates=ranking[:size],
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

    # ------------------------------------------------------------------
    # LLM path: heuristic floor + model-scored slots
    # ------------------------------------------------------------------

    def _select_with_llm(
        self,
        profiles: list[dict[str, Any]],
        summary: str,
        base: TargetSelection,
        graph: Any,
        suspects: list[str],
        forced: list[str],
        source_text: str | None,
        hints: list[str],
    ) -> TargetSelection:
        """The heuristic's list with its last ``llm_share`` replaced by the model's picks."""
        ranking = self._heuristic_ranking(profiles)
        size = len(base.gates)
        share = _llm_share()
        slots_total = min(size, max(1, round(size * share))) if share > 0 and size else 0
        floor = ranking[:size - slots_total]
        forced_set = set(forced)
        # Forced gates enter the list anyway; the model's slots are the tail
        # positions that are not, so both modes test exactly as many gates.
        slots = sum(1 for g in ranking[size - slots_total:size] if g not in forced_set)
        exclude = set(floor) | forced_set
        candidates = [p for p in profiles if str(p["gate_id"]) not in exclude]
        # XOR-shape matches not forced into the list are still worth showing.
        shown = [g for g in suspects if g not in exclude]

        scores, context, cached = self._score_with_llm(
            candidates, summary, graph, shown, source_text, len(profiles), len(floor)
        )
        if not scores:
            # The model ran but said nothing usable: not an LLM selection.
            base.context = context
            base.notes.append(
                "Model returned no usable gate scores; fell back to the structural heuristic."
            )
            return base
        candidate_ids = {str(p["gate_id"]) for p in candidates}
        hinted = [g for g in hints if g in candidate_ids]
        by_score = [g for g, s in sorted(scores.items(), key=lambda gs: (-gs[1], gs[0]))
                    if s >= _MIN_SCORE and g in candidate_ids]
        picks = list(dict.fromkeys(hinted + by_score))[:slots]
        chosen = set(picks)
        fill = [g for g in ranking[len(floor):] if g not in exclude and g not in chosen]
        fill = fill[:slots - len(picks)]

        context.update({
            "budget": len(floor) + slots,
            "floor": len(floor),
            "llm_slots": slots,
            "llm_picks": picks,
            "analysis_hints": [g for g in picks if g in set(hinted)],
            "heuristic_fill": fill,
        })
        selection = TargetSelection(
            gates=floor + picks + fill,
            strategy="llm (cached)" if cached else "llm",
            used_llm=True,
            cached=cached,
            context=context,
        )
        selection.notes.extend(base.notes)
        selection.notes.extend(context.get("notes", []))
        selection.notes.append(
            f"Fault list: the structural heuristic's top {len(floor)} gate(s), then "
            f"{len(picks)} of {slots} slot(s) filled by the model's highest-scored gates "
            f"among the {context.get('gates_sent', 0)} it scored"
            + (f" ({len(context['analysis_hints'])} from suspicious regions in its circuit "
               "analysis)" if context["analysis_hints"] else "")
            + (f", and {len(fill)} by the heuristic's next picks where the model scored "
               f"nothing at {_MIN_SCORE:g}+." if fill else ".")
        )
        if cached:
            selection.notes.append(
                "Gate scores were reused from the cache of an earlier run of this exact "
                "netlist through the same model, so this step cost no tokens. Set "
                "LLM_RESULT_CACHE=0 for a fresh model call (needed for repeat runs)."
            )
        return selection

    def _batch_size(self, candidates: list[dict[str, Any]], summary: str, limit: int) -> int:
        """Most gates one request can carry, prompt plus a score per gate."""
        size = min(limit, len(candidates)) if limit else len(candidates)
        budget = request_token_budget()
        if budget <= 0:
            return size
        sample = sorted(candidates, key=rarity_key)
        while size > _MIN_CONTEXT_POOL:
            prompt, _ = self._compose_prompt(sample[:size], summary)
            if estimate_prompt_tokens(prompt) + reply_budget(size * _TOKENS_PER_SCORE_LINE) <= budget:
                break
            size = max(_MIN_CONTEXT_POOL, int(size * 0.85))
        return size

    def _score_with_llm(
        self,
        candidates: list[dict[str, Any]],
        summary: str,
        graph: Any,
        shown: list[str],
        source_text: str | None,
        total_gates: int,
        floor: int,
    ) -> tuple[dict[str, float], dict[str, Any], bool]:
        """Ask the model to score every gate it is shown; returns (gate -> score, context, cached).

        A circuit too big for one request is split into up to ``llm_batches``
        independent batches, each scored in full. Nothing is trimmed between
        batches: the old carry-forward kept 34 gates per batch, and a gate
        dropped there was gone.
        """
        from pyatpg.core.progress import report

        limit = context_gate_limit(self.context_gate_limit)
        batch = self._batch_size(candidates, summary, limit)
        batches = llm_batches()
        total = len(candidates) if batch >= len(candidates) else min(len(candidates), batch * batches)
        pool, pool_notes = build_candidate_pool(candidates, graph, total, shown)
        slices = [pool[i:i + batch] for i in range(0, len(pool), batch)] if batch else []
        budget = request_token_budget()
        notes = list(pool_notes)
        if len(slices) > 1:
            notes.append(
                f"Scored in {len(slices)} batches of up to {batch} gates to fit the "
                f"{budget}-token request budget; every gate shown was scored. Set "
                f"{REQUEST_TOKEN_BUDGET_ENV_VAR} or the llm_batches setting to change this."
            )
        context: dict[str, Any] = {
            "gates_sent": len(pool),
            "gates_total": total_gates,
            "gate_limit": limit,
            "batches": len(slices),
            "notes": notes,
            "pool": [str(p["gate_id"]) for p in pool],
        }

        # Keyed on everything the question depends on: a different pool,
        # batch split, prompt or floor asks something else.
        cache_params = dict(
            model=self.provider.model_name, batch=batch, total=total, batches=len(slices),
            names="v2", prompt="v5", forced=len(shown), floor=floor,
        )
        cache = self.cache
        if cache is None and source_text is not None and cache_enabled():
            cache = LLMResultCache()
        if cache is not None and source_text is not None:
            hit = cache.get(TOKEN_STAGE, source_text, **cache_params)
            if isinstance(hit, dict) and isinstance(hit.get("scores"), dict):
                self.tracker.record_cached(TOKEN_STAGE)
                scores = {str(g): float(s) for g, s in hit["scores"].items()}
                context["scores"] = scores
                return scores, context, True

        scores: dict[str, float] = {}
        unscored = 0
        for index, chunk in enumerate(slices, start=1):
            report(f"Scoring gates with the LLM (batch {index} of {len(slices)})")
            got = self._ask_scores(chunk, summary)
            # A gate left out of the answer is a formatting slip, not a low
            # score: ask once more for exactly those.
            missing = [p for p in chunk if str(p["gate_id"]) not in got]
            if missing:
                got.update(self._ask_scores(missing, summary))
                unscored += sum(1 for p in missing if str(p["gate_id"]) not in got)
            scores.update(got)
        if unscored:
            notes.append(f"{unscored} gate(s) were still unscored after one re-ask.")
        context["scores"] = scores
        if cache is not None and source_text is not None and scores:
            cache.put(TOKEN_STAGE, source_text, {"scores": scores}, **cache_params)
        return scores, context, False

    def _ask_scores(self, rows: list[dict[str, Any]], summary: str) -> dict[str, float]:
        prompt, alias_to_gate = self._compose_prompt(rows, summary)
        started = time.perf_counter()
        response_text = self.provider.complete(
            prompt, max_output_tokens=reply_budget(len(rows) * _TOKENS_PER_SCORE_LINE)
        ) or ""
        self.tracker.record(
            TOKEN_STAGE, prompt, response_text,
            usage=getattr(self.provider, "last_usage", None),
            duration_s=time.perf_counter() - started,
        )
        return self._parse_scores(response_text, alias_to_gate)

    @staticmethod
    def _parse_scores(text: str, alias_to_gate: dict[str, str]) -> dict[str, float]:
        """Read "alias,score" lines back into real gate names -> score (0-100).

        Scores given on a 0-1 scale are read as fractions. Aliases listed
        without a score count as top picks (100). Aliases the model invents
        resolve to nothing and are dropped.
        """
        scores: dict[str, float] = {}
        for raw_line in text.replace(";", "\n").splitlines():
            parts = [p.strip() for p in raw_line.strip().split(",") if p.strip()]
            if not parts:
                continue
            if len(parts) == 2 and parts[0] in alias_to_gate:
                try:
                    value = float(parts[1])
                except ValueError:
                    value = 100.0
                if 0.0 <= value <= 1.0 and "." in parts[1]:
                    value *= 100.0
                scores.setdefault(alias_to_gate[parts[0]], max(0.0, min(100.0, value)))
                continue
            for token in parts:
                if token in alias_to_gate:
                    scores.setdefault(alias_to_gate[token], 100.0)
        return scores

    def _compose_prompt(
        self, rows: list[dict[str, Any]], summary: str
    ) -> tuple[str, dict[str, str]]:
        """The scoring prompt for exactly ``rows``; returns (prompt, alias -> real gate id).

        Gate names are anonymised before they reach the model. The
        framework's premise is that gate-level structural features are
        obfuscation-invariant -- that detection survives an adversary
        renaming every signal, which is what defeats RTL/source-level
        detectors. Sending the real names breaks that premise in the worst
        possible way here, because inserted logic in the generated corpus is
        named ht_trigger_out_N / HT_PAYLOAD_XOR and Trust-Hub's Tj_* / Trojan_*:
        a model could score perfectly by string-matching, and any measured
        result would be evidence about the naming convention rather than
        about structural reasoning. Opaque ids make the run identical to one
        on a renamed netlist.
        """
        alias_to_gate: dict[str, str] = {}
        table: list[tuple[str, dict[str, Any]]] = []
        for index, p in enumerate(sorted(rows, key=rarity_key)):
            alias = f"g{index}"
            alias_to_gate[alias] = str(p["gate_id"])
            table.append((alias, p.get("features", {}) or {}))
        prompt = f"""{summary}

You are assisting with test-pattern generation for a combinational circuit,
with the specific goal of catching logic that may have been maliciously
inserted (a hardware trojan) as well as ordinary hard-to-test faults.

A structural ranking has already chosen the circuit's rarest, hardest-to-
observe gates; they are not shown. Score each gate below for how much its
stuck-at faults are worth targeting explicitly in addition to those. Your
highest-scored gates take the remaining places in the fault list. Weigh two
different kinds of evidence:

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

Gates with a mark were flagged by deterministic analysis of the full
netlist; score them high unless the profile clearly contradicts the mark.

### Output format
Return exactly one line for EVERY gate in the table, as:

    gate_id,score

where score is an integer from 0 (not worth targeting) to 100 (certainly
worth it). Output nothing else -- no prose, no headers, no gate left out.

### Gate profiles
{COMPACT_LEGEND}
Rarest-toggling first.

{compact_profile_block(table)}
"""
        return prompt, alias_to_gate
