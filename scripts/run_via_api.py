#!/usr/bin/env python
"""Drive the *deployed* PyATPG backend end-to-end -- no local Atalanta, no
local LLM SDKs. You give it a base circuit; it uploads it to the website's
own API, which generates trojan variants, runs ATPG on all of them, and
ranks them -- all server-side, on whatever machine is already running the
backend. This script just orchestrates the calls, retries the flaky ones,
and assembles the paper table locally.

What you need on THIS machine: Python, `pip install requests networkx`,
and this repo checked out (only for its lightweight pyatpg.automation /
pyatpg.evaluation / pyatpg.analysis modules -- none of which need Atalanta
or an LLM SDK to import; see pyatpg/automation/api_client.py's docstring).
You do NOT need the Atalanta binary, and you do NOT need google-genai /
openai / anthropic installed -- the server has those, not you.

    python scripts/run_via_api.py --circuits benchmarks/incoming \\
        --api-url https://py-atpg-backend.onrender.com \\
        --llm-provider gemini --api-key "$GEMINI_API_KEY"

Same manifest/resumability/notification model as scripts/run_suite.py --
see docs/automation.md. The one thing worth knowing: the actual compute
runs on the server, so if that server is asleep (e.g. a free Render
instance that's been idle), the first request just takes longer to wake it
-- the retry/timeout budget here already accounts for that.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

# Allow running as `python scripts/run_via_api.py ...` from a repo checkout
# without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.automation.manifest import Manifest, fingerprint_file
from pyatpg.automation.notify import Notifier
from pyatpg.automation.research_pipeline import DEFAULT_STRATEGIES, _append_master_csv
from pyatpg.automation.retry import RetryPolicy, run_with_retries
from pyatpg.automation.suite import discover_circuits
from pyatpg.ranking.providers import AUTO_PROVIDER, PROVIDER_ORDER

try:
    from pyatpg.automation.api_client import ApiClient, ApiError
except ImportError as exc:
    print(f"{exc}\n\nThis script needs the 'requests' package: pip install requests")
    raise SystemExit(1)


def _build_notifier(no_notify: bool, output_dir: Path) -> Notifier:
    from pyatpg.automation.notify import ConsoleChannel

    log_path = str(output_dir / "notifications.log")
    if no_notify:
        return Notifier(channels=[ConsoleChannel(log_path=log_path)])
    try:
        return Notifier.from_env(log_path=log_path)
    except ValueError as exc:
        print(f"Notification config error, continuing without it: {exc}")
        return Notifier(channels=[ConsoleChannel(log_path=log_path)])


def _extract_dataset_zip(zip_bytes: bytes, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest_dir)


def _evaluate_variant(
    client: ApiClient, variant_dir: Path, ranker_name: str, k: int,
    llm_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Feature-extract + rank (both server-side) then evaluate against the
    variant's own metadata.json (local -- pure arithmetic, no server call
    needed for that part)."""
    from pyatpg.analysis.adapters import MetadataAdapter
    from pyatpg.evaluation.evaluator import Evaluator
    from pyatpg.ranking.models import RankingResult

    circuit_path = variant_dir / "circuit.v"
    meta_path = variant_dir / "metadata.json"
    if not circuit_path.exists() or not meta_path.exists():
        raise ValueError(f"{variant_dir} is missing circuit.v or metadata.json")

    feature_set = client.extract_features(circuit_path)
    ranking_json = client.run_ranking(feature_set, ranker_name, **llm_kwargs)
    ranking = RankingResult.from_dict(ranking_json)
    ground_truth = MetadataAdapter.parse_json(str(meta_path))
    result = Evaluator(k=k).evaluate(ranking, ground_truth)
    return result.to_dict()


def _run_one_circuit(
    circuit: Path,
    client: ApiClient,
    output_dir: Path,
    args: argparse.Namespace,
    llm_kwargs: dict[str, Any],
    log,
) -> dict[str, Any]:
    """Full pipeline for one circuit, run against the deployed backend.
    Returns a summary dict; raises on any failure (the caller's retry loop
    and manifest handle backoff/resumability)."""
    circuit_dir = output_dir / circuit.stem
    dataset_dir = circuit_dir / "dataset"

    total_variants = 0
    for strategy in args.strategies:
        log(f"    generating ({strategy})...")
        zip_bytes = client.generate_trojan_dataset(
            circuit, f"{circuit.stem}__{strategy}{circuit.suffix}",
            variants=args.variants_per_strategy, strategy=strategy,
            seed=args.seed, verify_activation=args.verify_activation,
        )
        _extract_dataset_zip(zip_bytes, dataset_dir)

    variant_dirs = sorted(dataset_dir.glob("*/trojan_*"))
    total_variants = len(variant_dirs)
    log(f"    {total_variants} trojan variant(s) generated across {len(args.strategies)} strategy(ies)")
    if total_variants == 0:
        raise ValueError(
            f"No trojan variant could be generated for {circuit.name} "
            "(likely too small/simple for the chosen strategies)"
        )

    atpg_rows = []
    files_to_run = [("clean", circuit)] + [(vd.name, vd / "circuit.v") for vd in variant_dirs]
    for label, path in files_to_run:
        log(f"    ATPG: {label}...")
        analysis = client.run_atpg(
            path, mode=args.mode, patterns=args.patterns, use_llm=not args.no_llm,
            backtrack_limit=args.backtrack_limit, **llm_kwargs,
        )
        (circuit_dir / "atpg" / f"{label}.json").parent.mkdir(parents=True, exist_ok=True)
        (circuit_dir / "atpg" / f"{label}.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        row = {"circuit": circuit.name, "variant": label}
        for run_key, run in (analysis.get("runs") or {}).items():
            results = run.get("results") or {}
            row[f"{run_key}_fault_coverage"] = results.get("fault_coverage")
        comparison = analysis.get("comparison")
        if comparison:
            row["targeted_coverage"] = comparison.get("targeted_coverage")
            row["non_targeted_coverage"] = comparison.get("non_targeted_coverage")
        atpg_rows.append(row)

    ranking_rows = []
    if not args.no_ranking:
        for vd in variant_dirs:
            log(f"    ranking: {vd.name}...")
            eval_row = _evaluate_variant(client, vd, args.ranker, args.k, llm_kwargs)
            eval_row["variant"] = vd.name
            ranking_rows.append(eval_row)

    _append_master_csv(output_dir, "master_atpg_summary.csv", circuit.name,
                        [{"source_circuit": circuit.name, **r} for r in atpg_rows])
    _append_master_csv(output_dir, "master_ranking_summary.csv", circuit.name,
                        [{"source_circuit": circuit.name, **r} for r in ranking_rows])

    return {
        "circuit": circuit.name,
        "variants_generated": total_variants,
        "atpg_rows": len(atpg_rows),
        "ranking_rows": len(ranking_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="https://py-atpg-backend.onrender.com",
                         help="Base URL of the deployed backend (default: the project's Render deployment).")
    parser.add_argument("--circuits", nargs="+", default=["benchmarks/incoming"],
                         help="Clean circuit files/dirs/globs to process (default: benchmarks/incoming).")
    parser.add_argument("--strategies", nargs="+", choices=DEFAULT_STRATEGIES, default=list(DEFAULT_STRATEGIES))
    parser.add_argument("--variants-per-strategy", type=int, default=10)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--verify-activation", action="store_true")
    parser.add_argument("--mode", choices=["targeted", "non_targeted", "compare"], default="compare")
    parser.add_argument("--patterns", type=int, default=3000)
    parser.add_argument("--backtrack-limit", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM assistance in the ATPG step.")
    parser.add_argument("--no-ranking", action="store_true", help="Skip the ranking/evaluation step.")
    parser.add_argument("--api-key", default=None, help="Your own LLM key -- sent per-request, never stored server-side.")
    parser.add_argument("--llm-provider", choices=[AUTO_PROVIDER, *PROVIDER_ORDER], default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--ranker", default="LLMRanker")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output-dir", default="results/api_research")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--base-delay", type=float, default=15.0)
    parser.add_argument("--max-delay", type=float, default=300.0)
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--watch", type=float, default=None, metavar="SECONDS")
    args = parser.parse_args(argv)

    llm_kwargs = {
        k: v for k, v in {
            "api_key": args.api_key, "llm_provider": args.llm_provider,
            "llm_model": args.llm_model, "llm_base_url": args.llm_base_url,
        }.items() if v
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    notifier = _build_notifier(args.no_notify, output_dir)
    client = ApiClient(args.api_url)
    policy = RetryPolicy(max_attempts=args.max_attempts, base_delay=args.base_delay, max_delay=args.max_delay)

    def run_once() -> int:
        circuits = discover_circuits(args.circuits)
        if not circuits:
            print(f"No circuit files (.v/.verilog/.bench) found in: {args.circuits}")
            return 1

        print(f"Checking {args.api_url} ...")
        if not client.health():
            print(f"Backend at {args.api_url} did not respond to /health -- it may be waking up "
                  "from sleep (free-tier instances do this) or genuinely down. Retries below will "
                  "still attempt it; if every circuit fails, check the URL and try again shortly.")

        manifest = Manifest(output_dir / "manifest.json")
        counts = {"done": 0, "failed": 0, "skipped": 0}

        for circuit in circuits:
            key = str(circuit)
            fingerprint = fingerprint_file(circuit)
            if not manifest.needs_run(key, fingerprint, force=args.force):
                counts["skipped"] += 1
                print(f"[skip] {circuit.name} (already done)")
                continue

            previous = manifest.get(key)
            manifest.start(key, fingerprint)
            print(f"\n=== {circuit.name} ===")

            def attempt():
                return _run_one_circuit(circuit, client, output_dir, args, llm_kwargs, print)

            def on_attempt(n, error):
                if error is not None:
                    print(f"  attempt {n} failed: {error}")

            outcome = run_with_retries(attempt, policy, on_attempt=on_attempt)

            if outcome.ok:
                manifest.record_success(key, fingerprint, outcome.attempts, outcome.value)
                if previous is not None and previous.status == "failed":
                    notifier.notify(f"{circuit.name}: recovered",
                                     f"Succeeded after previously failing ({previous.classification}).")
                counts["done"] += 1
                print(f"[done] {circuit.name} ({outcome.attempts} attempt(s))")
                continue

            classification = outcome.classification.value if outcome.classification else "unknown"
            manifest.record_failure(key, fingerprint, outcome.attempts, classification, outcome.error or "")
            already_known = (
                previous is not None and previous.status == "failed"
                and previous.classification == classification
            )
            if not already_known:
                if classification == "credentials":
                    notifier.notify(f"{circuit.name}: LLM account needs attention",
                                     f"{outcome.error}\n\nSwap the key or add credits; it will "
                                     "succeed on the next run without needing --force.", urgent=True)
                elif classification == "transient":
                    notifier.notify(f"{circuit.name}: still failing after retries this run",
                                     f"{outcome.error}\n\nWill retry automatically next run.")
            counts["failed"] += 1
            print(f"[fail] {circuit.name} ({classification}): {outcome.error}")

        print(f"\nDone: {counts['done']} succeeded, {counts['failed']} failed, {counts['skipped']} skipped.")
        print(f"Results: {output_dir / 'master_atpg_summary.csv'} and {output_dir / 'master_ranking_summary.csv'}")
        return 0 if counts["failed"] == 0 else 2

    if args.watch is None:
        return run_once()

    print(f"Watching (every {args.watch:.0f}s). Ctrl+C to stop.")
    try:
        while True:
            run_once()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
