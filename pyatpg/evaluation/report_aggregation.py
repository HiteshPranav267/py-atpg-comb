"""Turn a directory of saved ATPG reports into the paper's result tables.

The multi-circuit study's tables were transcribed by hand from ad-hoc scripts,
so the paper's numbers and the reports they came from could drift apart with
nothing to catch it. These functions read the reports and emit the same
tables, so a number in the paper can be regenerated rather than trusted.

Nothing here runs ATPG or contacts a model; it only reads reports that already
exist.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from pyatpg.evaluation.trojan_detection import (
    DETECTED,
    NOT_DETECTED_REDUNDANT,
    OUT_OF_SCOPE,
    classify_run,
    read_llm_provenance,
)

#: Directory names the study used for each run mode, matched case-insensitively
#: as substrings because the dataset's folders were named by hand and are not
#: consistent ("..._Atalanta_Outputs", "PyATG_...", "_LLM_Targetted_" etc.).
FULL_RUN_MARKERS = ("atalanta_outputs", "atg_")
TARGETED_MARKERS = ("llm_targe",)


@dataclass
class TrojanCase:
    """One trojan, as recorded by whichever runs were saved for it."""

    circuit: str
    variant: str
    strategy: str | None
    trigger_output: str
    full: dict[str, Any] | None = None
    targeted: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.full is not None and self.targeted is not None


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _body(report: dict) -> dict:
    section = report.get("atpg_report")
    return section if isinstance(section, dict) else report


def _run_of(report: dict, key: str) -> dict | None:
    return (_body(report).get("runs") or {}).get(key)


def discover_cases(root: Path) -> list[TrojanCase]:
    """Find every trojan directory under ``root`` and pair its two runs.

    The study's layout is ``<circuit>/<circuit>/trojan_NNNN/<run dir>/``, with
    a ``metadata.json`` beside the run directories describing the trojan.
    """
    cases: list[TrojanCase] = []
    for meta_path in sorted(root.glob("*/*/trojan_*/metadata.json")):
        meta = _load_json(meta_path)
        if not meta:
            continue
        variant_dir = meta_path.parent
        case = TrojanCase(
            circuit=str(meta.get("circuit") or variant_dir.parent.name),
            variant=variant_dir.name,
            strategy=meta.get("strategy"),
            trigger_output=str(meta.get("trigger_output") or ""),
            metadata=meta,
        )
        for run_dir in sorted(p for p in variant_dir.iterdir() if p.is_dir()):
            report = next(
                (_load_json(p) for p in sorted(run_dir.glob("pyatpg_report*.json"))),
                None,
            )
            if not report:
                continue
            lowered = run_dir.name.lower()
            if any(m in lowered for m in TARGETED_MARKERS):
                case.targeted = report
            elif any(m in lowered for m in FULL_RUN_MARKERS):
                case.full = report
        if case.full or case.targeted:
            cases.append(case)
    return cases


def timing_rows(cases: list[TrojanCase]) -> list[dict[str, Any]]:
    """Per-trojan CPU time and backtracking counts for both runs.

    ``speedup`` is full/targeted CPU seconds. ATALANTA reports its own CPU
    time at millisecond resolution, so on circuits this size the numerator and
    denominator are both small enough that the ratio carries real
    quantisation error -- recorded here, and qualified in :func:`timing_summary`
    rather than presented as a precise figure.
    """
    rows = []
    for case in cases:
        full = _run_of(case.full or {}, "non_targeted") if case.full else None
        targeted = _run_of(case.targeted or {}, "targeted") if case.targeted else None
        if not full or not targeted:
            continue
        f_cpu = (full.get("timing") or {}).get("total")
        t_cpu = (targeted.get("timing") or {}).get("total")
        f_res, t_res = full.get("results") or {}, targeted.get("results") or {}
        rows.append({
            "circuit": case.circuit.split("__")[0],
            "variant": case.variant,
            "strategy": case.strategy,
            "full_cpu_s": f_cpu,
            "targeted_cpu_s": t_cpu,
            "speedup": (f_cpu / t_cpu) if (f_cpu and t_cpu) else None,
            "full_backtracks": f_res.get("backtrackings"),
            "targeted_backtracks": t_res.get("backtrackings"),
            "full_patterns": f_res.get("patterns_after_compaction"),
            "targeted_patterns": t_res.get("patterns_after_compaction"),
            "pattern_reduction_pct": (
                100.0 * (1 - t_res["patterns_after_compaction"] / f_res["patterns_after_compaction"])
                if f_res.get("patterns_after_compaction") and t_res.get("patterns_after_compaction")
                else None
            ),
        })
    return rows


def _stats(values: list[float]) -> dict[str, float | None]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None, "stdev": None}
    return {
        "n": len(clean),
        "mean": statistics.mean(clean),
        "median": statistics.median(clean),
        "min": min(clean),
        "max": max(clean),
        "stdev": statistics.stdev(clean) if len(clean) > 1 else 0.0,
    }


def timing_summary(rows: list[dict[str, Any]], resolution_s: float = 0.001) -> dict[str, Any]:
    """Distributions for CPU time, speedup and backtracking.

    ``quantisation_warning`` fires when a meaningful share of runs finish
    within a few ticks of the engine's reporting resolution. A speedup ratio
    computed from 0.003s vs 0.001s is three ticks over one, and claiming
    "3x faster" from it would be reporting timer granularity as a result.
    """
    speedups = [r["speedup"] for r in rows]
    full_cpu = [r["full_cpu_s"] for r in rows]
    targeted_cpu = [r["targeted_cpu_s"] for r in rows]

    near_resolution = sum(
        1 for r in rows
        if r["targeted_cpu_s"] is not None and r["targeted_cpu_s"] <= resolution_s * 10
    )
    return {
        "cases": len(rows),
        "full_cpu_s": _stats(full_cpu),
        "targeted_cpu_s": _stats(targeted_cpu),
        "speedup": _stats(speedups),
        "full_backtracks": _stats([r["full_backtracks"] for r in rows]),
        "targeted_backtracks": _stats([r["targeted_backtracks"] for r in rows]),
        "pattern_reduction_pct": _stats([r["pattern_reduction_pct"] for r in rows]),
        "runs_within_10_ticks_of_resolution": near_resolution,
        "quantisation_warning": near_resolution > 0,
        "engine_time_resolution_s": resolution_s,
    }


def detection_rows(cases: list[TrojanCase]) -> list[dict[str, Any]]:
    """Per-trojan detection outcome for both runs, with LLM provenance."""
    rows = []
    for case in cases:
        row: dict[str, Any] = {
            "circuit": case.circuit.split("__")[0],
            "variant": case.variant,
            "strategy": case.strategy,
            "trigger_output": case.trigger_output,
            "full_detection": None,
            "targeted_detection": None,
            "excluded_reason": None,
            "used_llm": None,
            "llm_model": None,
        }
        if case.targeted:
            prov = read_llm_provenance(case.targeted)
            row["used_llm"] = prov.used_llm
            row["llm_model"] = prov.model
            # These reports were saved per mode, so a targeted report holds
            # only its own run -- classify_report wants a full run to compare
            # against and would reject it. Classify the one run directly,
            # honouring the same scope rule: a fault the targeting never
            # selected was never tested, whatever the undetected list says.
            run = _run_of(case.targeted, "targeted")
            if run is not None:
                selected = ((_body(case.targeted).get("targeting") or {}).get("gates")) or []
                outcome = classify_run(
                    run, case.trigger_output, in_scope=case.trigger_output in selected
                )
                row["targeted_detection"] = outcome.label
                row["targeted_gate_count"] = len(selected)
        if case.full:
            run = _run_of(case.full, "non_targeted")
            if run is not None:
                row["full_detection"] = classify_run(
                    run, case.trigger_output, in_scope=True
                ).label
        rows.append(row)
    return rows


# --------------------------------------------------------------- LaTeX

def _fmt(value: Any, spec: str = "", dash: str = "---") -> str:
    if value is None:
        return dash
    if spec:
        return format(value, spec)
    return str(value)


def latex_per_trojan_table(rows: list[dict[str, Any]], label: str = "tab:mc-full") -> str:
    """Table XI: every trojan's full-vs-targeted run."""
    lines = [
        r"\begin{table*}[t]",
        r"\caption{Full per-Trojan results: PyATPG full run vs.\ LLM-targeted run. "
        r"Generated from the run reports by "
        r"\texttt{scripts/summarize\_experiment\_reports.py}.}",
        rf"\label{{{label}}}",
        r"\centering",
        r"\renewcommand{\arraystretch}{1.05}",
        r"\footnotesize",
        r"\begin{tabular}{@{}llccccc@{}}",
        r"\toprule",
        r"\textbf{Circuit} & \textbf{Strategy} & \textbf{Trojan} & \textbf{Full pat.} & "
        r"\textbf{LLM pat.} & \textbf{Reduction} & \textbf{Full/targeted backtracks}\\",
        r"\midrule",
    ]
    for r in rows:
        strategy = (r.get("strategy") or "").replace("_", "-")
        trojan = r["variant"].replace("trojan_", "").lstrip("0") or "0"
        reduction = _fmt(r.get("pattern_reduction_pct"), ".1f")
        lines.append(
            f"{r['circuit']} & {strategy} & {trojan} & "
            f"{_fmt(r.get('full_patterns'))} & {_fmt(r.get('targeted_patterns'))} & "
            f"{reduction}\\% & "
            f"{_fmt(r.get('full_backtracks'))}/{_fmt(r.get('targeted_backtracks'))}\\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table*}"]
    return "\n".join(lines)


def latex_pattern_table(rows: list[dict[str, Any]], label: str = "tab:mc-patterns") -> str:
    """Table XII: compacted pattern count per circuit."""
    by_circuit: dict[str, list[dict]] = {}
    for r in rows:
        by_circuit.setdefault(r["circuit"], []).append(r)

    lines = [
        r"\begin{table}[t]",
        r"\caption{Compacted test-pattern count: PyATPG full run vs.\ LLM-targeted "
        r"run (mean per circuit). Generated from the run reports.}",
        rf"\label{{{label}}}",
        r"\centering",
        r"\renewcommand{\arraystretch}{1.15}",
        r"\begin{tabular}{@{}lrrr@{}}",
        r"\toprule",
        r"\textbf{Circuit} & \textbf{Full run} & \textbf{LLM-targeted} & "
        r"\textbf{Reduction}\\",
        r"\midrule",
    ]
    all_full, all_targeted = [], []
    for circuit in sorted(by_circuit):
        group = by_circuit[circuit]
        full = _stats([r.get("full_patterns") for r in group])
        targeted = _stats([r.get("targeted_patterns") for r in group])
        reduction = _stats([r.get("pattern_reduction_pct") for r in group])
        all_full += [r.get("full_patterns") for r in group]
        all_targeted += [r.get("targeted_patterns") for r in group]
        lines.append(
            f"{circuit} & {_fmt(full['mean'], '.1f')} & {_fmt(targeted['mean'], '.1f')} & "
            f"{_fmt(reduction['mean'], '.1f')}\\%\\\\"
        )
    overall = _stats([r.get("pattern_reduction_pct") for r in rows])
    lines += [
        r"\midrule",
        rf"\textbf{{Mean}} & {_fmt(_stats(all_full)['mean'], '.1f')} & "
        rf"{_fmt(_stats(all_targeted)['mean'], '.1f')} & "
        rf"\textbf{{{_fmt(overall['mean'], '.1f')}\%}}\\",
        r"\bottomrule", r"\end{tabular}", r"\end{table}",
    ]
    return "\n".join(lines)


def latex_detection_table(rows: list[dict[str, Any]], label: str = "tab:mc-detect") -> str:
    """Table XIII: detection outcomes per circuit."""
    by_circuit: dict[str, list[dict]] = {}
    for r in rows:
        by_circuit.setdefault(r["circuit"], []).append(r)

    lines = [
        r"\begin{table}[t]",
        r"\caption{Trojan-detection outcomes. ``Excluded'' counts trojans whose "
        r"trigger fault is provably redundant, or that the targeted run never "
        r"had in scope. Generated from the run reports.}",
        rf"\label{{{label}}}",
        r"\centering",
        r"\renewcommand{\arraystretch}{1.15}",
        r"\begin{tabular}{@{}lrrr@{}}",
        r"\toprule",
        r"\textbf{Circuit} & \textbf{Full detected} & \textbf{Targeted detected} & "
        r"\textbf{Excluded}\\",
        r"\midrule",
    ]
    tot_full = tot_tgt = tot_excl = tot_n = 0
    for circuit in sorted(by_circuit):
        group = by_circuit[circuit]
        full_ok = sum(1 for r in group if r["full_detection"] == DETECTED)
        tgt_ok = sum(1 for r in group if r["targeted_detection"] == DETECTED)
        excluded = sum(
            1 for r in group
            if r["targeted_detection"] in (NOT_DETECTED_REDUNDANT, OUT_OF_SCOPE)
            or r["full_detection"] == NOT_DETECTED_REDUNDANT
        )
        n = len(group)
        tot_full += full_ok; tot_tgt += tgt_ok; tot_excl += excluded; tot_n += n
        lines.append(f"{circuit} & {full_ok}/{n} & {tgt_ok}/{n} & {excluded}\\\\")
    lines += [
        r"\midrule",
        rf"\textbf{{Total}} & \textbf{{{tot_full}/{tot_n}}} & "
        rf"\textbf{{{tot_tgt}/{tot_n}}} & \textbf{{{tot_excl}}}\\",
        r"\bottomrule", r"\end{tabular}", r"\end{table}",
    ]
    return "\n".join(lines)
