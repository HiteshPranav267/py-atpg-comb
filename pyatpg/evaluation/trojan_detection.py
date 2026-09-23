"""Classify whether an ATPG run detected an inserted trojan, and whether the
evidence for a budget-constrained detection advantage actually holds.

Coverage percentages cannot answer "was *this* trojan found". A targeted run
reports coverage over the handful of faults it was given, a non-targeted run
over the whole circuit, and neither denominator mentions the trojan. What does
answer it is the per-fault disposition Atalanta reports for the trojan's own
trigger output, read against the fault list that run was actually given.

Two distinctions carry the whole result and are easy to get wrong:

*Absence is not detection.* A gate missing from a run's undetected list only
means "detected" if the fault was in that run's list to begin with. The
non-targeted run covers the whole collapsed list, so absence there is a
detection; the targeted run covers only the selected gates, so absence there
may mean the fault was never tested. That case is :data:`OUT_OF_SCOPE`, and
scoring it as a detection silently inflates targeted performance.

*Aborted is not redundant.* ``redundant`` means Atalanta proved no pattern can
detect the fault -- an unactivatable trojan, which no method can find and which
must be excluded from the comparison rather than counted as a miss.
``aborted`` means it gave up within the search budget, which is the only
disposition that can support a budget-constrained claim -- and even then only
alongside a control run proving a larger budget does find it
(:func:`qualifies_three_leg`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The fault was in this run's list and Atalanta produced a pattern for it.
DETECTED = "DETECTED"
#: In the list, but Atalanta gave up within the search budget.
NOT_DETECTED_ABORTED = "NOT_DETECTED(aborted)"
#: In the list, and Atalanta proved no pattern can detect it.
NOT_DETECTED_REDUNDANT = "NOT_DETECTED(redundant)"
#: Never in this run's fault list, so this run says nothing either way.
OUT_OF_SCOPE = "OUT_OF_SCOPE"

#: Atalanta's ``-U`` file cannot tell a redundant fault from an aborted one
#: (see pyatpg.atpg.parsing.parse_fault_dispositions), so its entries arrive
#: labelled "undetected". They are aborted faults, and are read as such.
_ABORTED_STATUSES = {"aborted", "undetected"}


class HeuristicContamination(RuntimeError):
    """A run that was meant to use an LLM did not use one.

    Raised rather than returned: a sweep that quietly mixes heuristic and LLM
    runs produces a table that cannot be interpreted at all, and the failure
    is silent precisely because the heuristic fallback is designed to keep
    working when no model is reachable.
    """


@dataclass
class LlmProvenance:
    """What actually ranked the gates, as recorded by the run itself."""

    used_llm: bool
    strategy: str | None
    provider: str | None
    model: str | None
    api_calls: int
    cached_hits: int
    tokens: int
    cached: bool

    @property
    def consulted_model(self) -> bool:
        """True when a model produced this selection, fresh or from cache."""
        return bool(self.used_llm) and (self.api_calls > 0 or self.cached_hits > 0)


@dataclass
class RunOutcome:
    """One ATPG run's verdict on one trojan, plus what it cost."""

    label: str
    dispositions: list[dict[str, str]] = field(default_factory=list)
    in_scope: bool = True
    backtracks: int | None = None
    cpu_s: float | None = None
    patterns: int | None = None
    applied_backtrack_limit: int | None = None
    aborted_faults: int | None = None
    redundant_faults: int | None = None

    @property
    def detected(self) -> bool:
        return self.label == DETECTED


@dataclass
class CaseOutcome:
    """A full-vs-targeted comparison of one trojan at one search budget."""

    circuit: str
    variant: str
    strategy: str | None
    trigger_output: str
    budget: int | None
    full: RunOutcome
    targeted: RunOutcome | None
    llm: LlmProvenance
    targeted_gate_count: int = 0

    @property
    def budgets_match(self) -> bool:
        """Both runs given the same per-fault budget, as a controlled
        comparison requires. False means the comparison is confounded."""
        if self.targeted is None:
            return True
        return self.full.applied_backtrack_limit == self.targeted.applied_backtrack_limit

    @property
    def excluded_reason(self) -> str | None:
        """Why this case cannot support a detection claim, if it cannot."""
        if self.full.label == NOT_DETECTED_REDUNDANT or (
            self.targeted is not None
            and self.targeted.label == NOT_DETECTED_REDUNDANT
        ):
            return "trojan is redundant (provably untestable), not stealthy"
        if self.targeted is not None and self.targeted.label == OUT_OF_SCOPE:
            return "targeting never selected the trigger, so the targeted run never tested it"
        if not self.budgets_match:
            return (
                f"search budgets differ (full={self.full.applied_backtrack_limit}, "
                f"targeted={self.targeted.applied_backtrack_limit})"
            )
        return None


def _atpg_section(report: dict[str, Any]) -> dict[str, Any]:
    """The analysis body, whether the file wraps it or is it.

    ``AtpgAnalysis.to_dict()`` is the body itself; the suite writes it nested
    under ``atpg_report`` alongside ranking and feature-extraction sections.
    """
    section = report.get("atpg_report")
    return section if isinstance(section, dict) else report


def trojan_footprint(metadata: dict[str, Any]) -> set[str]:
    """Every net an insertion introduced or displaced.

    The payload splices a gate into an existing net: the new gate takes that
    net's name and the original driver is renamed ``<net>_orig``, so the
    trojan's structural footprint is wider than the trigger output alone.
    """
    names = {
        metadata[key]
        for key in ("trigger_output", "affected_net", "payload_target")
        if metadata.get(key)
    }
    target = metadata.get("payload_target")
    if target:
        names.add(f"{target}_orig")
    return names


def read_llm_provenance(report: dict[str, Any]) -> LlmProvenance:
    """What the report says about which ranker chose the targeted gates."""
    body = _atpg_section(report)
    targeting = body.get("targeting") or {}
    usage = body.get("llm_usage") or {}
    return LlmProvenance(
        used_llm=bool(targeting.get("used_llm")),
        strategy=targeting.get("strategy"),
        provider=usage.get("provider"),
        model=usage.get("model"),
        api_calls=int(usage.get("api_calls") or 0),
        cached_hits=int(usage.get("cached_hits") or 0),
        tokens=int(usage.get("total_tokens_est") or 0),
        cached=bool(targeting.get("cached")),
    )


def assert_llm_provenance(
    report: dict[str, Any],
    expected_provider: str | None = None,
    expected_model: str | None = None,
    allow_cached: bool = True,
) -> LlmProvenance:
    """Raise unless this report was produced by the intended model.

    Checks the run's own record rather than the caller's intent: passing
    ``use_llm=True`` proves nothing, because the ranker falls back to the
    offline heuristic whenever no provider can be built -- which is what a
    missing key looks like, and it does not raise.

    ``expected_provider``/``expected_model`` are compared case-insensitively
    and as substrings, so "gemini" matches the "Google Gemini" label the
    provider records.
    """
    prov = read_llm_provenance(report)
    if not prov.used_llm:
        raise HeuristicContamination(
            f"targeting used {prov.strategy!r} (used_llm=False), not an LLM. "
            "A missing or unreachable API key silently produces this."
        )
    if not prov.consulted_model:
        raise HeuristicContamination(
            f"targeting claims used_llm=True but records no model call "
            f"(api_calls={prov.api_calls}, cached_hits={prov.cached_hits})."
        )
    if prov.cached and not allow_cached:
        raise HeuristicContamination(
            "selection was reused from cache and a fresh model call was required."
        )
    if expected_provider and expected_provider.lower() not in (prov.provider or "").lower():
        raise HeuristicContamination(
            f"provider was {prov.provider!r}, expected {expected_provider!r}."
        )
    if expected_model and (prov.model or "") != expected_model:
        raise HeuristicContamination(
            f"model was {prov.model!r}, expected {expected_model!r}."
        )
    return prov


def classify_run(
    run: dict[str, Any], trigger_output: str, in_scope: bool
) -> RunOutcome:
    """Label one run's outcome for one trojan's trigger fault."""
    dispositions = [
        {"fault": f.get("fault_name"), "status": f.get("status")}
        for f in (run.get("faults") or [])
        if f.get("gate") == trigger_output
    ]
    if dispositions:
        statuses = {d["status"] for d in dispositions}
        # A redundancy proof is the stronger statement: if any fault on the
        # trigger is provably undetectable the trojan is unactivatable, and
        # no budget makes it findable.
        label = (
            NOT_DETECTED_REDUNDANT
            if "redundant" in statuses
            else NOT_DETECTED_ABORTED
            if statuses & _ABORTED_STATUSES
            else NOT_DETECTED_ABORTED
        )
    else:
        label = DETECTED if in_scope else OUT_OF_SCOPE

    results = run.get("results") or {}
    timing = run.get("timing") or {}
    parameters = run.get("parameters") or {}
    return RunOutcome(
        label=label,
        dispositions=dispositions,
        in_scope=in_scope,
        backtracks=results.get("backtrackings"),
        cpu_s=timing.get("total"),
        patterns=results.get("patterns_after_compaction"),
        applied_backtrack_limit=parameters.get("backtrack_limit"),
        aborted_faults=results.get("aborted_faults"),
        redundant_faults=results.get("redundant_faults"),
    )


def classify_report(
    report: dict[str, Any],
    metadata: dict[str, Any],
    budget: int | None = None,
    variant: str = "",
) -> CaseOutcome:
    """Classify a compare-mode report against the trojan that was inserted."""
    body = _atpg_section(report)
    runs = body.get("runs") or {}
    targeting = body.get("targeting") or {}
    selected = targeting.get("gates") or []
    trigger = metadata.get("trigger_output") or ""

    full_run = runs.get("non_targeted")
    targeted_run = runs.get("targeted")
    if full_run is None:
        raise ValueError("report has no non-targeted run to compare against")

    return CaseOutcome(
        circuit=(metadata.get("circuit") or "").split("__")[0],
        variant=variant,
        strategy=metadata.get("strategy"),
        trigger_output=trigger,
        budget=budget,
        full=classify_run(full_run, trigger, in_scope=True),
        targeted=(
            classify_run(targeted_run, trigger, in_scope=trigger in selected)
            if targeted_run is not None
            else None
        ),
        llm=read_llm_provenance(report),
        targeted_gate_count=len(selected),
    )


def qualifies_three_leg(case: CaseOutcome, full_at_high_budget: RunOutcome) -> bool:
    """Does this case evidence a budget-constrained detection advantage?

    All three legs are required, and the third is what separates a real
    result from the two ways of faking one:

    1. the full run, at budget B, aborted the trojan's trigger fault;
    2. the targeted run, at *the same* B, detected it, having actually had
       it in scope;
    3. the full run, given a much larger budget, detects it -- proving leg 1
       was the budget running out and not an untestable trojan.
    """
    if case.excluded_reason is not None:
        return False
    if case.targeted is None:
        return False
    return (
        case.full.label == NOT_DETECTED_ABORTED
        and case.targeted.label == DETECTED
        and full_at_high_budget.label == DETECTED
    )
