"""The full "drop in a clean circuit, get a paper-ready comparison" pipeline.

Per circuit: generate trojan-infected variants (across every insertion
strategy the codebase has -- "all sorts of trojan infections", not one),
run the ATPG pipeline over the clean circuit and every variant, then run a
ranking benchmark comparing an LLM-backed ranker's guesses against the
ground truth the generator recorded. Composes the existing, already
retry-and-resume-safe :func:`~pyatpg.automation.suite.run_atpg_suite` and
:func:`~pyatpg.automation.suite.run_ranking_suite` rather than re-deriving
their retry/skip logic.

Directory layout per circuit, under ``output_dir/<circuit_stem>/``::

    dataset/    the generated trojan variants (BenchmarkRunner's expected
                layout: <circuit>__<strategy>/{clean,trojan_NNNN}/)
    staged/     flat copies with unique filenames, for run_atpg_suite (the
                generator writes every variant as literally "circuit.v",
                which collide once flattened into one ATPG batch)
    atpg/       run_atpg_suite's own output: summary.csv, per-variant JSON,
                manifest.json
    ranking/    run_ranking_suite's own output: BenchmarkRunner's
                benchmark_<ranker>.{json,csv}, manifest.json

Two master CSVs at ``output_dir`` roll every circuit's ATPG and ranking
results together for the paper. They're reported separately rather than
force-joined into one row: ``BenchmarkRunner`` evaluates a ranker once per
*strategy folder*, not once per individual trojan variant inside it (an
existing property of that class, not something introduced here), so a
single unified row would imply more precision than the data actually has.
"""
from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .manifest import fingerprint_file
from .notify import Notifier, null_notifier
from .retry import RetryPolicy
from .suite import run_atpg_suite, run_ranking_suite

#: Every insertion strategy pyatpg.trojan.strategy currently implements.
DEFAULT_STRATEGIES = ("random", "rare_node", "low_scoap")


@dataclass(frozen=True)
class GenerationConfig:
    """How to turn one clean circuit into a trojan-variant dataset.

    Every strategy in ``strategies`` runs, each attempting
    ``variants_per_strategy`` insertions -- covering different
    trigger/payload shapes rather than just one -- so a ranker or ATPG
    comparison in the paper isn't only validated against a single style of
    trojan.
    """

    strategies: tuple[str, ...] = DEFAULT_STRATEGIES
    variants_per_strategy: int = 10
    #: Fixes where trojans go, for a reproducible dataset. None draws from
    #: OS entropy -- fine for a single run, but means a --force re-run
    #: produces a *different* dataset, not the same one regenerated.
    seed: int | None = None
    verify_activation: bool = False

    def fingerprint(self) -> str:
        return (
            f"{','.join(self.strategies)}::{self.variants_per_strategy}::"
            f"{self.seed}::{self.verify_activation}"
        )


def _dataset_is_current(dataset_dir: Path, fingerprint: str) -> bool:
    marker = dataset_dir / ".generation.json"
    if not marker.exists():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("fingerprint") == fingerprint
    except (json.JSONDecodeError, OSError):
        return False


def _generate_dataset(
    circuit: Path, dataset_dir: Path, config: GenerationConfig, full_fingerprint: str, log: Callable[[str], None],
) -> int:
    """Write ``<circuit>__<strategy>/{clean,trojan_NNNN}/`` per strategy.

    Regenerating always starts from a clean directory: a half-written
    dataset from an interrupted run must never be mistaken for a complete
    one on the next attempt.
    """
    from pyatpg.api.services.trojan_service import TrojanService

    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    dataset_dir.mkdir(parents=True, exist_ok=True)

    source_text = circuit.read_text(encoding="utf-8")
    total = 0
    for strategy in config.strategies:
        result = TrojanService.generate_dataset(
            source_text=source_text,
            circuit_name=f"{circuit.stem}__{strategy}",
            variants=config.variants_per_strategy,
            strategy=strategy,
            filename=circuit.name,
            output_dir=str(dataset_dir),
            seed=config.seed,
            verify_activation=config.verify_activation,
        )
        generated = result["variants_generated"]
        log(f"    {strategy}: {generated}/{config.variants_per_strategy} variant(s) generated")
        total += generated

    (dataset_dir / ".generation.json").write_text(
        json.dumps({"fingerprint": full_fingerprint, "total_variants": total}), encoding="utf-8",
    )
    return total


def _stage_for_atpg(circuit: Path, dataset_dir: Path, staging_dir: Path) -> list[Path]:
    """Flatten the dataset into uniquely-named files for run_atpg_suite.

    Every variant the generator writes is literally named ``circuit.v`` --
    fine nested under its own ``trojan_NNNN/`` folder, but run_atpg_suite
    names its outputs (and CSV rows) from each path's stem, so a flat batch
    of same-named files would silently overwrite each other's results.
    """
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    staged = [staging_dir / f"{circuit.stem}__clean{circuit.suffix}"]
    shutil.copyfile(circuit, staged[0])

    for variant_dir in sorted(dataset_dir.glob("*/trojan_*")):
        src = variant_dir / "circuit.v"
        if not src.exists():
            continue
        strategy_folder = variant_dir.parent.name  # "<circuit>__<strategy>"
        dst = staging_dir / f"{strategy_folder}__{variant_dir.name}.v"
        shutil.copyfile(src, dst)
        staged.append(dst)

    return staged


def _append_master_csv(output_dir: Path, filename: str, source_circuit: str, rows: list[dict[str, Any]]) -> None:
    """Merge one circuit's rows into the master CSV, replacing its old ones.

    Read-filter-rewrite rather than a true append: every existing row
    tagged with this ``source_circuit`` is dropped first, so re-running a
    circuit (a retry, a --force, an edited file) replaces its old rows
    instead of duplicating them. Other circuits' rows are carried over
    unchanged. Column set is the union of whatever's already in the file
    and whatever this circuit just produced -- different circuits can
    legitimately have different columns (e.g. only a targeted run has
    targeted_fault_coverage), and a plain DictWriter would otherwise raise
    on the mismatch.
    """
    path = output_dir / filename
    existing: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            existing = [r for r in reader if r.get("source_circuit") != source_circuit]
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    if not existing and not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing)
        writer.writerows(rows)


def run_research_suite(
    circuits: list[Path],
    output_dir: Path,
    gen_config: GenerationConfig,
    atpg_run_one: Callable[[Path], Any],
    atpg_policy: RetryPolicy,
    build_ranker: Callable[[], Any],
    ranking_policy: RetryPolicy,
    *,
    atpg_llm_config_of: Callable[[Path], Any] | None = None,
    ranker_k: int = 5,
    force: bool = False,
    notifier: Notifier | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Run the full generate -> ATPG -> rank pipeline over every circuit.

    ``atpg_run_one`` and ``build_ranker`` are the same shape as the plain
    ``atpg``/``ranking`` subcommands use -- see ``scripts/run_suite.py`` --
    so this differs from running them separately only in also generating
    the trojan dataset each circuit's ATPG/ranking steps run against, and
    in rolling every circuit into two master CSVs.

    Returns ``{"done": n, "failed": n, "skipped": n}`` counting whole
    circuits (all of generation, every variant's ATPG run, and the ranking
    benchmark succeeding) rather than individual variants.
    """
    from pyatpg.evaluation.benchmark import BenchmarkRunner

    output_dir.mkdir(parents=True, exist_ok=True)
    notifier = notifier or null_notifier()
    counts = {"done": 0, "failed": 0, "skipped": 0}

    for circuit in circuits:
        circuit_dir = output_dir / circuit.stem
        dataset_dir = circuit_dir / "dataset"
        staging_dir = circuit_dir / "staged"
        atpg_dir = circuit_dir / "atpg"
        ranking_dir = circuit_dir / "ranking"

        config_fp = f"{fingerprint_file(circuit)}::{gen_config.fingerprint()}"
        already_generated = _dataset_is_current(dataset_dir, config_fp) and not force

        log(f"\n=== {circuit.name} ===")
        if already_generated:
            log("[skip] dataset already generated for this circuit + config")
        else:
            log("[run]  generating trojan dataset "
                f"({', '.join(gen_config.strategies)}, {gen_config.variants_per_strategy}/strategy)")
            try:
                total = _generate_dataset(circuit, dataset_dir, gen_config, config_fp, log)
            except Exception as exc:  # noqa: BLE001 - a bad circuit shouldn't stop the batch
                log(f"[fail] {circuit.name}: dataset generation failed: {exc}")
                notifier.notify(f"{circuit.name}: trojan generation failed", str(exc), urgent=False)
                counts["failed"] += 1
                continue
            if total == 0:
                log(f"[fail] {circuit.name}: no trojan variant could be generated "
                    "(circuit likely too small/simple for these strategies)")
                counts["failed"] += 1
                continue

        staged = _stage_for_atpg(circuit, dataset_dir, staging_dir)
        log(f"[run]  ATPG over {len(staged)} file(s) (1 clean + variants)")
        atpg_counts = run_atpg_suite(
            staged, atpg_run_one, atpg_dir, atpg_policy,
            force=force, llm_config_of=atpg_llm_config_of, log=log, notifier=notifier,
        )

        log(f"[run]  ranking benchmark over the generated dataset")
        ranker = build_ranker()

        def run_dataset():
            runner = BenchmarkRunner(
                dataset_dir=str(dataset_dir), ranker=ranker, output_dir=str(ranking_dir), k=ranker_k,
            )
            return runner.run()

        ranking_counts = run_ranking_suite(
            dataset_dir, run_dataset, ranking_dir, ranking_policy, force=force, log=log, notifier=notifier,
        )

        atpg_rows: list[dict[str, Any]] = []
        atpg_summary_csv = atpg_dir / "summary.csv"
        if atpg_summary_csv.exists():
            with atpg_summary_csv.open(newline="", encoding="utf-8") as handle:
                atpg_rows = [{"source_circuit": circuit.name, **row} for row in csv.DictReader(handle)]
        _append_master_csv(output_dir, "master_atpg_summary.csv", circuit.name, atpg_rows)

        ranker_name = getattr(ranker, "name", ranker.__class__.__name__)
        ranking_json = ranking_dir / f"benchmark_{ranker_name}.json"
        ranking_rows: list[dict[str, Any]] = []
        if ranking_json.exists():
            ranking_rows = [
                {"source_circuit": circuit.name, **row}
                for row in json.loads(ranking_json.read_text(encoding="utf-8"))
            ]
        _append_master_csv(output_dir, "master_ranking_summary.csv", circuit.name, ranking_rows)

        circuit_ok = atpg_counts["failed"] == 0 and ranking_counts["failed"] == 0
        if circuit_ok:
            counts["done"] += 1
            log(f"[done] {circuit.name}: pipeline complete")
        else:
            counts["failed"] += 1
            log(f"[fail] {circuit.name}: {atpg_counts['failed']} ATPG failure(s), "
                f"{ranking_counts['failed']} ranking failure(s) -- see {atpg_dir}/manifest.json "
                f"and {ranking_dir}/manifest.json")

    notifier.notify(
        "Research suite finished",
        f"{counts['done']} circuit(s) fully complete, {counts['failed']} still have failures "
        f"(will retry automatically next run), {counts['skipped']} skipped.",
        urgent=False,
    )
    return counts
