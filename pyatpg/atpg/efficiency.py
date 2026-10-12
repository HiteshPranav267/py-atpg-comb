"""What each test set costs and what it buys: standard vs targeted ATPG.

A compare-mode run with the LLM produces three test sets from the same
netlist: standard ATPG over every fault, the targeted list chosen without
the model, and the targeted list chosen with it, both at the same budget.
This module puts them side by side on the numbers a test-cost claim needs:

* patterns, and **tester cycles** -- under full scan each pattern is
  scanned in, clocked once and scanned out, so applying a test set costs
  about patterns x (scan cells + 1) clock cycles on every chip tested;
* generation time (Atalanta, plus feature extraction and the LLM where the
  run used them), paid once per design;
* whole-circuit coverage of each pattern set, so a smaller set is never
  mistaken for a complete test;
* for a design whose Trojan gates are known by name, how many of the
  Trojan's stuck-at faults each set detects, how many of its patterns (in
  order) it takes to detect all of them, and the fewest patterns that would
  have sufficed (greedy set cover -- an oracle bound that needs the ground
  truth, reported so "standard ATPG needed 3000 patterns" is not read as
  "standard ATPG cannot do it in fewer").
"""
from __future__ import annotations

from typing import Any

from pyatpg.core.netlist import Circuit

from .faultsim import detection_masks
from .models import AtalantaRun, TestVectorSet

#: Above this many (fault, detecting pattern) pairs the greedy minimum is
#: skipped: building it is linear in that count and runs in Python.
GREEDY_PAIR_CAP = 4_000_000


def trojan_fault_detection(circuit: Circuit, vectors: TestVectorSet,
                           trojan_nets: list[str]) -> dict[str, Any]:
    """How a pattern set detects the stuck-at faults on the Trojan's nets."""
    referenced = {i for g in circuit.gates.values() for i in g.inputs}
    sites = sorted(n for n in set(trojan_nets)
                   if n in circuit.gates or (n in circuit.inputs and n in referenced))
    if not sites or vectors.count == 0:
        return {"unavailable_reason": "no Trojan nets or no patterns"}
    try:
        masks = detection_masks(circuit, vectors, sites)
    except Exception as exc:  # noqa: BLE001 - a metric must not fail the run
        return {"unavailable_reason": f"simulation failed: {exc}"}
    detected = [m for m in masks.values() if m]
    # Lowest set bit = first pattern (in file order) detecting the fault.
    first_all = max(((m & -m).bit_length() for m in detected), default=0)
    out: dict[str, Any] = {
        "trojan_faults": len(masks),
        "detected": len(detected),
        "patterns": vectors.count,
        "patterns_to_detect_all": first_all,
        "undetected": sorted(f"{s} /{v}" for (s, v), m in masks.items() if not m)[:30],
    }
    pairs = sum(bin(m).count("1") for m in detected)
    out["min_patterns_oracle"] = _greedy_cover(detected, vectors.count) if pairs <= GREEDY_PAIR_CAP else None
    return out


def _greedy_cover(masks: list[int], n_patterns: int) -> int:
    """Patterns a greedy set cover needs to detect every fault in ``masks``."""
    by_pattern: dict[int, int] = {}
    for index, m in enumerate(masks):
        while m:
            low = m & -m
            p = low.bit_length() - 1
            by_pattern[p] = by_pattern.get(p, 0) | (1 << index)
            m ^= low
    remaining = (1 << len(masks)) - 1
    chosen = 0
    while remaining:
        best = max(by_pattern.values(), key=lambda b: bin(b & remaining).count("1"))
        if not best & remaining:
            break
        remaining &= ~best
        chosen += 1
    return chosen


def _run_row(run: AtalantaRun, *, gates: int | None, extra_s: float, chain: int,
             circuit: Circuit, truth: list[str]) -> dict[str, Any]:
    patterns = run.results.patterns_after_compaction
    if patterns is None:
        patterns = run.test_vectors.count
    atpg_s = run.timing.total or 0.0
    row: dict[str, Any] = {
        "gates_targeted": gates,
        "faults": run.results.collapsed_faults,
        "fault_coverage": run.results.fault_coverage,
        "aborted": run.results.aborted_faults,
        "patterns": patterns,
        "tester_cycles": patterns * (chain + 1),
        "atpg_s": round(atpg_s, 3),
        "generation_s": round(atpg_s + extra_s, 3),
        "simulated_coverage": (run.pattern_coverage or {}).get("coverage"),
    }
    if truth:
        row["trojan"] = trojan_fault_detection(circuit, run.test_vectors, truth)
    return row


def three_way(circuit: Circuit, *, standard: AtalantaRun | None, llm: AtalantaRun | None,
              no_llm: AtalantaRun | None, llm_gates: list[str], no_llm_gates: list[str],
              extraction_s: float, llm_s: float, truth: list[str]) -> dict[str, Any]:
    """The comparison table: standard / targeted without LLM / targeted with LLM."""
    chain = len(getattr(circuit, "scan_cells", None) or {})
    rows: dict[str, Any] = {}
    if standard is not None:
        rows["standard"] = _run_row(standard, gates=None, extra_s=0.0, chain=chain,
                                    circuit=circuit, truth=truth)
    if no_llm is not None:
        rows["targeted_no_llm"] = _run_row(no_llm, gates=len(no_llm_gates), extra_s=extraction_s,
                                           chain=chain, circuit=circuit, truth=truth)
    if llm is not None:
        rows["targeted_llm"] = _run_row(llm, gates=len(llm_gates), extra_s=extraction_s + llm_s,
                                        chain=chain, circuit=circuit, truth=truth)
    base = rows.get("standard", {}).get("patterns")
    for row in rows.values():
        if base and row.get("patterns") is not None:
            row["pattern_reduction_pct"] = round(100.0 * (1 - row["patterns"] / base), 2)
    return {
        "scan_cells": chain,
        "runs": rows,
        "notes": [
            "tester_cycles = patterns x (scan cells + 1): each full-scan pattern is shifted "
            "in, captured in one clock and shifted out (overlapping the next shift-in). "
            "This is the per-chip cost; generation_s is paid once per design.",
            "generation_s: Atalanta alone for standard ATPG; plus feature extraction for "
            "the targeted lists; plus the LLM's wall-clock time for the LLM list.",
            "simulated_coverage is each pattern set over the same whole-circuit fault list: "
            "a targeted set is a Trojan test, not a replacement for the full test.",
            "trojan.patterns_to_detect_all counts patterns in the order the tool wrote them; "
            "trojan.min_patterns_oracle is a greedy minimum that needs the ground truth, so "
            "no tool could achieve it without knowing where the Trojan is.",
        ],
    }
