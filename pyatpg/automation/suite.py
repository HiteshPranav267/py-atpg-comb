"""Batch drivers: discover circuits, run each with retries, keep a manifest.

Two suites:

- :func:`run_atpg_suite` -- per-circuit granularity. Each circuit gets its
  own retry budget, its own manifest record, and its own result file, so a
  paper table can be built from whatever has finished at any point.
- :func:`run_ranking_suite` -- wraps the existing
  :class:`pyatpg.evaluation.benchmark.BenchmarkRunner`, which evaluates a
  whole trojan dataset in one pass, with the same retry-with-backoff and a
  one-record manifest (there is no natural per-circuit split without
  changing that class's own iteration, which is out of scope here).
"""
from __future__ import annotations

import csv
import glob as _glob
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .manifest import Manifest, fingerprint_file
from .retry import RetryOutcome, RetryPolicy, run_with_retries

#: Extensions treated as circuit files when a directory (rather than a
#: glob pattern or an explicit file) is passed in.
_CIRCUIT_EXTENSIONS = (".v", ".verilog", ".bench")


def discover_circuits(patterns: Iterable[str]) -> list[Path]:
    """Resolve glob patterns / directories / files into a deduped file list.

    A directory contributes every circuit file directly inside it (not
    recursively -- the ``incoming/`` inbox and ``benchmarks/`` are both
    flat), in sorted order, so the same inputs always produce the same run
    order and the same manifest keys.
    """
    seen: set[Path] = set()
    ordered: list[Path] = []

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        if resolved.suffix.lower() not in _CIRCUIT_EXTENSIONS:
            return
        seen.add(resolved)
        ordered.append(resolved)

    for pattern in patterns:
        p = Path(pattern)
        if p.is_dir():
            for child in sorted(p.iterdir()):
                add(child)
            continue
        if any(c in pattern for c in "*?["):
            # stdlib glob (not Path.glob) so absolute patterns work on
            # every platform -- Path.glob rejects a pattern with a drive
            # or root on Windows.
            matches = sorted(Path(m) for m in _glob.glob(pattern))
        else:
            matches = [p]
        for match in matches:
            add(match)

    return ordered


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _run_summary_row(circuit: Path, analysis: Any, elapsed: float, llm_config) -> dict[str, Any]:
    """Flatten an AtpgAnalysis into one CSV-friendly row for the paper."""
    payload = analysis.to_dict()
    row: dict[str, Any] = {
        "circuit": circuit.name,
        "job_id": payload.get("job_id", ""),
        "mode": payload.get("mode", ""),
        "elapsed_s": round(elapsed, 2),
        "llm_provider": llm_config.provider if llm_config else "",
        "llm_model": llm_config.model if llm_config else "",
    }
    for run_key, run in (payload.get("runs") or {}).items():
        results = run.get("results") or {}
        row[f"{run_key}_fault_coverage"] = results.get("fault_coverage")
        row[f"{run_key}_pattern_count"] = results.get("pattern_count")
    comparison = payload.get("comparison")
    if comparison:
        row["targeted_coverage"] = comparison.get("targeted_coverage")
        row["non_targeted_coverage"] = comparison.get("non_targeted_coverage")
    sequential = payload.get("sequential")
    if sequential:
        seq_results = (sequential.get("analysis") or {}).get("results") or {}
        row["sequential_fault_coverage"] = seq_results.get("fault_coverage")
        seq_comparison = sequential.get("comparison")
        if seq_comparison:
            baseline = (seq_comparison.get("baseline") or {}).get("results") or {}
            assisted = (seq_comparison.get("llm_assisted") or {}).get("results") or {}
            row["sequential_baseline_fault_coverage"] = baseline.get("fault_coverage")
            row["sequential_llm_assisted_fault_coverage"] = assisted.get("fault_coverage")
    row["notes"] = " | ".join(payload.get("notes") or [])
    return row


def _rewrite_summary_csv(output_dir: Path, manifest: Manifest) -> None:
    """Regenerate the whole CSV from the manifest, so it never has stale or
    duplicate rows regardless of retry/rerun history -- cheap at the scale
    this is meant for (tens to low hundreds of circuits)."""
    rows = [
        r.result for r in sorted(manifest.all_records(), key=lambda r: r.key)
        if r.status == "done" and r.result
    ]
    csv_path = output_dir / "summary.csv"
    if not rows:
        return
    fieldnames: list[str] = []
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_atpg_suite(
    circuits: list[Path],
    run_one: Callable[[Path], Any],
    output_dir: Path,
    policy: RetryPolicy,
    *,
    force: bool = False,
    llm_config_of: Callable[[Path], Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Run ``run_one(circuit_path) -> AtpgAnalysis`` over every circuit.

    Persists progress to ``output_dir`` after every circuit: a per-circuit
    JSON with the full analysis, a manifest.json with retry/resume state,
    and a summary.csv rewritten from whatever has succeeded so far.

    Stops early (without touching later circuits) on a
    :class:`~pyatpg.automation.retry.Classification.FATAL` error -- that
    means the engine itself is unusable, so every remaining circuit would
    fail the same way.

    Returns counts: ``{"done": n, "failed": n, "skipped": n}``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(output_dir / "manifest.json")
    counts = {"done": 0, "failed": 0, "skipped": 0}

    for circuit in circuits:
        key = str(circuit)
        fingerprint = fingerprint_file(circuit)
        if not manifest.needs_run(key, fingerprint, force=force):
            counts["skipped"] += 1
            log(f"[skip] {circuit.name} (already done)")
            continue

        manifest.start(key, fingerprint)
        log(f"[run]  {circuit.name}")
        started = time.monotonic()
        attempt_log: list[str] = []

        def attempt() -> Any:
            return run_one(circuit)

        def on_attempt(n: int, error: BaseException | None) -> None:
            if error is not None:
                msg = f"  attempt {n} failed: {error}"
                attempt_log.append(msg)
                log(msg)

        outcome: RetryOutcome = run_with_retries(attempt, policy, on_attempt=on_attempt)
        elapsed = time.monotonic() - started

        if outcome.ok:
            analysis = outcome.value
            llm_config = llm_config_of(circuit) if llm_config_of else None
            row = _run_summary_row(circuit, analysis, elapsed, llm_config)
            _write_json(output_dir / f"{circuit.stem}.json", analysis.to_dict())
            manifest.record_success(key, fingerprint, outcome.attempts, row)
            _rewrite_summary_csv(output_dir, manifest)
            counts["done"] += 1
            log(f"[done] {circuit.name} in {elapsed:.1f}s ({outcome.attempts} attempt(s))")
            continue

        if outcome.classification and outcome.classification.value == "fatal":
            manifest.record_failure(key, fingerprint, outcome.attempts, "fatal", outcome.error or "")
            log(f"[FATAL] {circuit.name}: {outcome.error}")
            log("Stopping the run: this failure means the engine itself is unusable, "
                "not that this circuit is bad -- every remaining circuit would fail "
                "the same way.")
            break

        classification = outcome.classification.value if outcome.classification else "unknown"
        manifest.record_failure(key, fingerprint, outcome.attempts, classification, outcome.error or "")
        counts["failed"] += 1
        verb = "will retry on next run" if classification == "transient" else "will NOT auto-retry"
        log(f"[fail] {circuit.name} ({classification}, {verb}): {outcome.error}")

    return counts


def run_ranking_suite(
    dataset_dir: Path,
    run_dataset: Callable[[], list[Any]],
    output_dir: Path,
    policy: RetryPolicy,
    *,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Run a whole-dataset ranking benchmark with retry-with-backoff.

    ``run_dataset`` should call ``BenchmarkRunner(...).run()`` (it already
    writes its own JSON/CSV under ``output_dir``); this adds the retry loop
    and a one-record manifest so a finished run isn't silently repeated.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(output_dir / "manifest.json")
    key = str(dataset_dir)
    fingerprint = "dataset"  # whole-directory granularity: no per-file fingerprint

    if not manifest.needs_run(key, fingerprint, force=force):
        log(f"[skip] {dataset_dir} (already completed this dataset)")
        return {"done": 0, "failed": 0, "skipped": 1}

    manifest.start(key, fingerprint)
    log(f"[run]  ranking benchmark over {dataset_dir}")

    def on_attempt(n: int, error: BaseException | None) -> None:
        if error is not None:
            log(f"  attempt {n} failed: {error}")

    outcome = run_with_retries(run_dataset, policy, on_attempt=on_attempt)

    if outcome.ok:
        results = outcome.value or []
        manifest.record_success(key, fingerprint, outcome.attempts, {"circuits_evaluated": len(results)})
        log(f"[done] ranking benchmark: {len(results)} result(s) ({outcome.attempts} attempt(s))")
        return {"done": 1, "failed": 0, "skipped": 0}

    classification = outcome.classification.value if outcome.classification else "unknown"
    manifest.record_failure(key, fingerprint, outcome.attempts, classification, outcome.error or "")
    log(f"[fail] ranking benchmark ({classification}): {outcome.error}")
    return {"done": 0, "failed": 1, "skipped": 0}
