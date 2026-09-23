#!/usr/bin/env python
"""Unattended batch runner: trojan generation, ATPG, and ranking benchmarks.

Designed to be triggered repeatedly (Windows Task Scheduler, cron, or just
re-run by hand) without redoing finished work, and to keep retrying a
circuit through transient failures -- an LLM 503/429, a dropped connection
-- instead of giving up on the first one. When an LLM account is out of
credits or the key is invalid, that's detected separately and surfaced as
an urgent notification rather than retried pointlessly.

Three subcommands:

    atpg      Run the ATPG pipeline over a batch of circuits.
    ranking   Run a ranking benchmark over an existing trojan dataset.
    research  The full pipeline, per circuit: generate trojan variants
              (every insertion strategy), run ATPG over the clean circuit
              and every variant, then rank them with an LLM and compare
              against ground truth -- everything a paper table needs from
              one clean circuit dropped in.

Typical use for a research paper -- drop clean circuit files into
``benchmarks/incoming/`` (or point --circuits at any folder/glob), then
either run this by hand or schedule it:

    python scripts/run_suite.py research --circuits benchmarks/incoming \\
        --llm-provider gemini --api-key "$GEMINI_API_KEY"

Everything lands in --output-dir: per-circuit detail plus master CSVs you
can pull straight into a paper table. Re-running the same command only
processes what's new, changed, or still retryable -- see --force to redo
everything anyway.

Set NOTIFY_DISCORD_WEBHOOK and/or NOTIFY_EMAIL_TO (+ NOTIFY_EMAIL_SMTP_*)
to get pinged when a circuit needs attention, instead of only finding out
by checking the output later; --no-notify disables this even if those are
set. See docs/automation.md for the Task Scheduler / cron setup, the
--watch flag's tradeoffs, and the full notification config.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running as `python scripts/run_suite.py ...` from a repo checkout
# without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.automation import (
    GenerationConfig, RetryPolicy, discover_circuits, run_atpg_suite, run_ranking_suite,
    run_research_suite,
)
from pyatpg.automation.notify import Notifier
from pyatpg.ranking.providers import AUTO_PROVIDER, PROVIDER_ORDER

#: Kept in sync by hand with pyatpg.trojan.strategy's concrete strategies --
#: that module doesn't register a name->class map, so there's nothing to
#: introspect this from.
STRATEGY_CHOICES = ("random", "rare_node", "low_scoap")


def _retry_policy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-attempts", type=int, default=6,
        help="Retries for a transient failure within this invocation (default: 6). "
        "Still failing after this many, it's recorded as retryable and picked up "
        "fresh -- with this same budget -- the next time the suite runs, so "
        "scheduling it periodically gives effectively unlimited persistence "
        "across a deadline. The research subcommand applies this to both its "
        "ATPG and ranking steps.",
    )
    parser.add_argument("--base-delay", type=float, default=15.0,
                         help="Seconds before the first retry (default: 15). Doubles each attempt.")
    parser.add_argument("--max-delay", type=float, default=300.0,
                         help="Cap on the backoff delay in seconds (default: 300 = 5 min).")


def _notify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-notify", action="store_true",
        help="Disable Discord/email notifications even if NOTIFY_* environment variables "
        "are set; console output and the notifications.log file still happen. See "
        "docs/automation.md#notifications.",
    )


def _build_notifier(args: argparse.Namespace, output_dir: Path) -> Notifier:
    from pyatpg.automation.notify import ConsoleChannel

    log_path = str(output_dir / "notifications.log")
    if args.no_notify:
        return Notifier(channels=[ConsoleChannel(log_path=log_path)])
    try:
        return Notifier.from_env(log_path=log_path)
    except ValueError as exc:
        # A misconfigured channel (e.g. NOTIFY_EMAIL_TO set with no SMTP
        # host) shouldn't take the whole run down -- fall back to
        # console-only and say why, rather than crashing before any circuit
        # even starts.
        print(f"Notification config error, continuing without it: {exc}")
        return Notifier(channels=[ConsoleChannel(log_path=log_path)])


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
    notifier = _build_notifier(args, output_dir)
    counts = run_atpg_suite(
        circuits, run_one, output_dir, policy, force=args.force,
        llm_config_of=llm_config_of, notifier=notifier,
    )
    print(f"\nDone: {counts['done']} succeeded, {counts['failed']} failed, {counts['skipped']} skipped.")
    print(f"Results: {output_dir / 'summary.csv'}")
    return 0 if counts["failed"] == 0 else 2


def _cmd_ranking(args: argparse.Namespace) -> int:
    from pyatpg.api.services.ranking_service import RankingService
    from pyatpg.evaluation.benchmark import BenchmarkRunner

    policy = RetryPolicy(max_attempts=args.max_attempts, base_delay=args.base_delay, max_delay=args.max_delay)
    output_dir = Path(args.output_dir)
    notifier = _build_notifier(args, output_dir)

    def run_dataset():
        ranker = RankingService.get_ranker(
            args.ranker, api_key=args.api_key, llm_provider=args.llm_provider,
            llm_model=args.llm_model, llm_base_url=args.llm_base_url,
        )
        runner = BenchmarkRunner(
            dataset_dir=args.dataset_dir, ranker=ranker, output_dir=str(output_dir), k=args.k,
        )
        return runner.run()

    counts = run_ranking_suite(
        Path(args.dataset_dir), run_dataset, output_dir, policy, force=args.force, notifier=notifier,
    )
    print(f"\nDone: {counts['done']} succeeded, {counts['failed']} failed, {counts['skipped']} skipped.")
    return 0 if counts["failed"] == 0 else 2


def _cmd_research(args: argparse.Namespace) -> int:
    from pyatpg.api.services.ranking_service import RankingService
    from pyatpg.atpg import AnalysisMode, AtpgAnalysisService
    from pyatpg.ranking.providers import resolve_llm_config

    circuits = discover_circuits(args.circuits)
    if not circuits:
        print(f"No circuit files (.v/.verilog/.bench) found in: {args.circuits}")
        return 1
    print(f"Found {len(circuits)} clean circuit(s) to generate trojan variants for.")

    gen_config = GenerationConfig(
        strategies=tuple(args.strategies), variants_per_strategy=args.variants_per_strategy,
        seed=args.seed, verify_activation=args.verify_activation,
    )

    service = AtpgAnalysisService(
        api_key=args.api_key, llm_provider=args.llm_provider,
        llm_model=args.llm_model, llm_base_url=args.llm_base_url,
        effort_ladder=() if args.no_targeted_escalation else None,
    )
    mode = AnalysisMode(args.mode)

    def atpg_run_one(path: Path):
        return service.run(
            source_text=path.read_text(encoding="utf-8"), filename=path.name, mode=mode,
            toggle_patterns=args.patterns, use_llm=not args.no_llm, backtrack_limit=args.backtrack_limit,
        )

    def atpg_llm_config_of(_path: Path):
        if args.no_llm:
            return None
        try:
            return resolve_llm_config(
                provider=args.llm_provider, api_key=args.api_key,
                model=args.llm_model, base_url=args.llm_base_url,
            )
        except ValueError:
            return None

    def build_ranker():
        return RankingService.get_ranker(
            args.ranker, api_key=args.api_key, llm_provider=args.llm_provider,
            llm_model=args.llm_model, llm_base_url=args.llm_base_url,
        )

    policy = RetryPolicy(max_attempts=args.max_attempts, base_delay=args.base_delay, max_delay=args.max_delay)
    output_dir = Path(args.output_dir)
    notifier = _build_notifier(args, output_dir)

    counts = run_research_suite(
        circuits, output_dir, gen_config, atpg_run_one, policy, build_ranker, policy,
        atpg_llm_config_of=atpg_llm_config_of, ranker_k=args.k, force=args.force,
        notifier=notifier,
    )
    print(f"\nDone: {counts['done']} circuit(s) fully complete, "
          f"{counts['failed']} still have failures, {counts['skipped']} skipped.")
    print(f"Master results: {output_dir / 'master_atpg_summary.csv'} and "
          f"{output_dir / 'master_ranking_summary.csv'}")
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
    _notify_args(atpg)
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
    _notify_args(ranking)
    ranking.set_defaults(func=_cmd_ranking, circuits=None)

    research = sub.add_parser(
        "research", help="Full pipeline: generate trojan variants, run ATPG, and rank them -- per circuit.",
    )
    research.add_argument("--circuits", nargs="+", default=["benchmarks/incoming"],
                           help="Clean circuit files to build datasets from (default: benchmarks/incoming).")
    research.add_argument("--strategies", nargs="+", choices=STRATEGY_CHOICES, default=list(STRATEGY_CHOICES),
                           help="Trojan insertion strategies to generate variants with (default: all three, "
                           "for coverage across different trigger/payload shapes).")
    research.add_argument("--variants-per-strategy", type=int, default=10,
                           help="Attempted trojan insertions per strategy per circuit (default: 10). Some may "
                           "be skipped if no valid insertion point exists on a small circuit.")
    research.add_argument("--seed", type=int, default=None,
                           help="Fixes where trojans are inserted, for a reproducible dataset. Omit to draw "
                           "from OS entropy (then --force regenerates a *different* dataset, not the same one).")
    research.add_argument("--verify-activation", action="store_true",
                           help="Confirm each trojan's trigger is actually reachable via an extra ATPG job per "
                           "insertion (roughly doubles generation time; off by default).")
    research.add_argument("--mode", choices=["targeted", "non_targeted", "compare"], default="compare")
    research.add_argument("--patterns", type=int, default=3000)
    research.add_argument("--backtrack-limit", type=int, default=None)
    research.add_argument(
        "--no-targeted-escalation", action="store_true",
        help="Pin the targeted run to --backtrack-limit instead of climbing the "
        "targeted effort ladder. Needed for a controlled comparison where both "
        "runs get the same per-fault search budget.")
    research.add_argument("--no-llm", action="store_true",
                           help="Skip LLM assistance in the ATPG step (the ranking step still needs an LLM "
                           "ranker unless --ranker names a non-LLM one, e.g. ScoapRanker).")
    research.add_argument("--api-key", default=None)
    research.add_argument("--llm-provider", choices=[AUTO_PROVIDER, *PROVIDER_ORDER], default=None)
    research.add_argument("--llm-model", default=None)
    research.add_argument("--llm-base-url", default=None)
    research.add_argument("--ranker", default="LLMRanker")
    research.add_argument("--k", type=int, default=5)
    research.add_argument("--output-dir", default="results/research")
    research.add_argument("--force", action="store_true",
                           help="Regenerate datasets and re-run everything, ignoring all manifests.")
    _retry_policy_args(research)
    _notify_args(research)
    research.set_defaults(func=_cmd_research, dataset_dir=None)

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
