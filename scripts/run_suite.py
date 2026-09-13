#!/usr/bin/env python
"""Unattended batch runner for ATPG analyses and ranking benchmarks.

Designed to be triggered repeatedly (Windows Task Scheduler, cron, or just
re-run by hand) without redoing finished work, and to keep retrying a
circuit through transient failures -- an LLM 503/429, a dropped connection
-- instead of giving up on the first one.

Typical use for a research paper: drop circuit files into
``benchmarks/incoming/`` (or point --circuits at any folder/glob), then
either run this by hand or schedule it:

    python scripts/run_suite.py atpg --circuits benchmarks/incoming \\
        --mode compare --llm-provider gemini --api-key "$GEMINI_API_KEY"

Everything lands in --output-dir (default results/suite): a JSON per
circuit, a manifest.json driving resumability, and a summary.csv you can
pull straight into a paper table. Re-running the same command only
processes circuits that are new, changed, or still retryable -- see
--force to redo everything anyway.

See docs/automation.md for the Task Scheduler / cron setup and the
--watch flag's tradeoffs.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running as `python scripts/run_suite.py ...` from a repo checkout
# without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.automation import RetryPolicy, discover_circuits, run_atpg_suite, run_ranking_suite
from pyatpg.ranking.providers import AUTO_PROVIDER, PROVIDER_ORDER


def _retry_policy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-attempts", type=int, default=6,
        help="Retries per circuit for transient failures within this invocation "
        "(default: 6). A circuit that still fails after this many is recorded "
        "as retryable and picked up fresh -- with this same budget -- the next "
        "time the suite runs, so scheduling it periodically gives effectively "
        "unlimited persistence across a deadline.",
    )
    parser.add_argument("--base-delay", type=float, default=15.0,
                         help="Seconds before the first retry (default: 15). Doubles each attempt.")
    parser.add_argument("--max-delay", type=float, default=300.0,
                         help="Cap on the backoff delay in seconds (default: 300 = 5 min).")


def _cmd_atpg(args: argparse.Namespace) -> int:
    from pyatpg.atpg import AnalysisMode, AtpgAnalysisService
    from pyatpg.ranking.providers import resolve_llm_config

    circuits = discover_circuits(args.circuits)
    if not circuits:
        print(f"No circuit files (.v/.verilog/.bench) found in: {args.circuits}")
        return 1
    print(f"Found {len(circuits)} circuit(s).")

    service = AtpgAnalysisService(
        api_key=args.api_key,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
    )
    mode = AnalysisMode(args.mode)

    def run_one(path: Path):
        source_text = path.read_text(encoding="utf-8")
        return service.run(
            source_text=source_text,
            filename=path.name,
            mode=mode,
            toggle_patterns=args.patterns,
            use_llm=not args.no_llm,
            backtrack_limit=args.backtrack_limit,
        )

    def llm_config_of(_path: Path):
        if args.no_llm:
            return None
        try:
            return resolve_llm_config(
                provider=args.llm_provider, api_key=args.api_key,
                model=args.llm_model, base_url=args.llm_base_url,
            )
        except ValueError:
            return None

    policy = RetryPolicy(max_attempts=args.max_attempts, base_delay=args.base_delay, max_delay=args.max_delay)
    output_dir = Path(args.output_dir)
    counts = run_atpg_suite(
        circuits, run_one, output_dir, policy, force=args.force, llm_config_of=llm_config_of,
    )
    print(f"\nDone: {counts['done']} succeeded, {counts['failed']} failed, {counts['skipped']} skipped.")
    print(f"Results: {output_dir / 'summary.csv'}")
    return 0 if counts["failed"] == 0 else 2


def _cmd_ranking(args: argparse.Namespace) -> int:
    from pyatpg.api.services.ranking_service import RankingService
    from pyatpg.evaluation.benchmark import BenchmarkRunner

    policy = RetryPolicy(max_attempts=args.max_attempts, base_delay=args.base_delay, max_delay=args.max_delay)
    output_dir = Path(args.output_dir)

    def run_dataset():
        ranker = RankingService.get_ranker(
            args.ranker, api_key=args.api_key, llm_provider=args.llm_provider,
            llm_model=args.llm_model, llm_base_url=args.llm_base_url,
        )
        runner = BenchmarkRunner(
            dataset_dir=args.dataset_dir, ranker=ranker, output_dir=str(output_dir), k=args.k,
        )
        return runner.run()

    counts = run_ranking_suite(Path(args.dataset_dir), run_dataset, output_dir, policy, force=args.force)
    print(f"\nDone: {counts['done']} succeeded, {counts['failed']} failed, {counts['skipped']} skipped.")
    return 0 if counts["failed"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--watch", type=float, default=None, metavar="SECONDS",
        help="Instead of running once, loop forever: re-scan --circuits/--dataset-dir every "
        "SECONDS and process anything new. Runs in this terminal until stopped (Ctrl+C) -- "
        "see docs/automation.md for running this as a background process instead of a "
        "periodic scheduled task.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    atpg = sub.add_parser("atpg", help="Run the ATPG pipeline over a batch of circuits.")
    atpg.add_argument("--circuits", nargs="+", default=["benchmarks/incoming"],
                       help="Glob pattern(s), file(s), or directory/directories to scan "
                       "(default: benchmarks/incoming).")
    atpg.add_argument("--mode", choices=["targeted", "non_targeted", "compare"], default="compare")
    atpg.add_argument("--patterns", type=int, default=3000,
                       help="Random patterns for toggle-rate profiling (default: 3000).")
    atpg.add_argument("--backtrack-limit", type=int, default=None)
    atpg.add_argument("--no-llm", action="store_true", help="Skip LLM assistance; structural methods only.")
    atpg.add_argument("--api-key", default=None)
    atpg.add_argument("--llm-provider", choices=[AUTO_PROVIDER, *PROVIDER_ORDER], default=None)
    atpg.add_argument("--llm-model", default=None)
    atpg.add_argument("--llm-base-url", default=None)
    atpg.add_argument("--output-dir", default="results/suite")
    atpg.add_argument("--force", action="store_true", help="Re-run every circuit, ignoring the manifest.")
    _retry_policy_args(atpg)
    atpg.set_defaults(func=_cmd_atpg, dataset_dir=None)

    ranking = sub.add_parser("ranking", help="Run a ranking benchmark over a trojan dataset.")
    ranking.add_argument("--dataset-dir", required=True,
                          help="Directory of circuit_name/trojan_*/{circuit.v,metadata.json}.")
    ranking.add_argument("--ranker", default="LLMRanker")
    ranking.add_argument("--k", type=int, default=5)
    ranking.add_argument("--api-key", default=None)
    ranking.add_argument("--llm-provider", choices=[AUTO_PROVIDER, *PROVIDER_ORDER], default=None)
    ranking.add_argument("--llm-model", default=None)
    ranking.add_argument("--llm-base-url", default=None)
    ranking.add_argument("--output-dir", default="results/ranking")
    ranking.add_argument("--force", action="store_true", help="Re-run even if this dataset already completed.")
    _retry_policy_args(ranking)
    ranking.set_defaults(func=_cmd_ranking, circuits=None)

    args = parser.parse_args(argv)

    if args.watch is None:
        return args.func(args)

    print(f"Watching (every {args.watch:.0f}s). Ctrl+C to stop.")
    try:
        while True:
            rc = args.func(args)
            if rc == 1:
                # "nothing found" isn't worth logging every poll; the loop
                # below still waits normally and tries again.
                pass
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
