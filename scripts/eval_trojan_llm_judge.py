"""Can an LLM rank trojan trigger-payload candidates better than a rarity rule?

Hypothesis (fixed before any test run):
    Given the same deterministic candidate set, an LLM judging each candidate's
    anonymised local context ranks the true trojan higher than the relaxed
    rarity rule -- specifically on OR and MUX payloads, which the XOR-only
    structural detector cannot see.

For every trojan variant and every clean base circuit:
  1. generate candidates (rare, single-load gate -> 2-input gate), identical
     for every method;
  2. score them with each method:
       relaxed -- trigger rarity x input-activity imbalance (hand rule)
       random  -- uniform scores, averaged over several seeds
       llm     -- the model's probability, from the local context
  3. record where the true trigger-payload pair ranks.

Metrics per payload shape and method:
  * top-1 / top-3 / top-5 localization and MRR (ties broken at the mean);
  * per-variant AUROC (fraction of ordinary candidates ranked below the trojan);
  * detection at zero false alarms: a variant counts as detected when its
    highest candidate score exceeds the highest score on every clean circuit
    of the same split;
  * paired detection: the variant scores above its own clean circuit.

Uses the datasets eval_trojan_verdict.py generated (same trigger nets and
target net across payload shapes, so shapes are directly comparable).

Examples:
  # develop on c432/c880 (not reported as results)
  python scripts/eval_trojan_llm_judge.py --split dev --variants 2 --llm
  # the held-out test, once the prompt is frozen
  python scripts/eval_trojan_llm_judge.py --split test --variants 5 --llm --llm-wait 30
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_trojan_verdict import analyse  # noqa: E402
from llm_quota import call_with_quota_wait  # noqa: E402
from pyatpg.evaluation.trojan_candidates import (  # noqa: E402
    candidate_context,
    generate_candidates,
    is_trojan_candidate,
    relaxed_rule_score,
)
from pyatpg.evaluation.trojan_llm_judge import PROMPT_VERSION, TrojanCandidateJudge  # noqa: E402

SPLITS = {
    "dev": ["c432", "c880"],
    "test": ["c1908", "c2670", "s1238", "s5378"],
}
SHAPES = {
    "xor": "results/trojan_verdict/datasets",
    "or": "results/trojan_verdict_or/datasets",
    "mux": "results/trojan_verdict_mux/datasets",
    "xor4": "results/trojan_verdict_wide/datasets",
}
STRATEGIES = ("random", "rare_node", "low_scoap")
RANDOM_SEEDS = 5


def rank_of(scores: list[float], true_idx: int) -> float:
    """1-based rank of the true candidate, ties broken at their mean position."""
    t = scores[true_idx]
    greater = sum(1 for i, s in enumerate(scores) if i != true_idx and s > t)
    ties = sum(1 for i, s in enumerate(scores) if i != true_idx and s == t)
    return 1 + greater + ties / 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=list(SPLITS), required=True)
    ap.add_argument("--circuits", nargs="+", default=None, help="override the split's circuits")
    ap.add_argument("--shapes", nargs="+", default=["xor", "or", "mux"], choices=list(SHAPES))
    ap.add_argument("--variants", type=int, default=5, help="variants per insertion strategy")
    ap.add_argument("--context", default="B", choices=["A", "B", "C"],
                    help="what the LLM sees: A stats only, B + neighbourhood, C + SCOAP")
    ap.add_argument("--max-nodes", type=int, default=14)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--llm-wait", type=float, default=0.0,
                    help="hours to wait out LLM quota limits (0 = stop on a quota error)")
    ap.add_argument("--provider", default="env", choices=["env", "cerebras"],
                    help="env: the app's configured LLM (.env). cerebras: gpt-oss-120b on "
                         "Cerebras's free tier, key from CEREBRAS_API_KEY -- bigger requests, "
                         "so a whole circuit's candidates go in one prompt")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="max candidates per request (default 20; 80 with --provider cerebras)")
    ap.add_argument("--prompt-token-budget", type=int, default=None,
                    help="estimated prompt tokens per request (default 5200 for Groq's "
                         "8000-token cap; 20000 with --provider cerebras)")
    ap.add_argument("--llm-rpm", type=float, default=None,
                    help="pace requests to at most this many per minute "
                         "(default unlimited; 4.5 with --provider cerebras, whose free tier allows 5)")
    ap.add_argument("--toggle-patterns", type=int, default=3000)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    cerebras = args.provider == "cerebras"
    if args.batch_size is None:
        args.batch_size = 80 if cerebras else 20
    if args.prompt_token_budget is None:
        args.prompt_token_budget = 20000 if cerebras else 5200
    if args.llm_rpm is None and cerebras:
        args.llm_rpm = 4.5

    circuits = args.circuits or SPLITS[args.split]
    out = args.out or Path(f"results/trojan_llm_judge/{args.split}_{args.context}")
    out.mkdir(parents=True, exist_ok=True)

    judge = None
    model = "-"
    if args.llm:
        from pyatpg.ranking.providers import create_provider, resolve_llm_config

        if cerebras:
            key = os.environ.get("CEREBRAS_API_KEY", "").strip()
            if not key:
                print("--provider cerebras needs CEREBRAS_API_KEY in the environment or .env.",
                      file=sys.stderr)
                return 2
            config = resolve_llm_config(provider="openai_compatible", api_key=key,
                                        model="gpt-oss-120b", base_url="https://api.cerebras.ai/v1")
        else:
            config = resolve_llm_config()
        if config is None:
            print("--llm given but no LLM key is configured.", file=sys.stderr)
            return 2
        provider = create_provider(config)
        model = config.model

        min_gap = 60.0 / args.llm_rpm if args.llm_rpm else 0.0
        last_call = [0.0]

        def complete(prompt: str, n_candidates: int) -> str:
            def call():
                # Client-side pacing: cheaper than hitting a per-minute
                # limit and waiting out its penalty.
                gap = time.monotonic() - last_call[0]
                if gap < min_gap:
                    time.sleep(min_gap - gap)
                last_call[0] = time.monotonic()
                # Reply reservation scales with the batch actually sent: it
                # counts against the provider's per-request token cap.
                return provider.complete(prompt, json_mode=True,
                                         max_output_tokens=45 * n_candidates + 150)
            return call_with_quota_wait(call, args.llm_wait * 3600) if args.llm_wait else call()

        complete.last_usage = lambda: getattr(provider, "last_usage", None)

        judge = TrojanCandidateJudge(
            complete, model, Path("results/trojan_llm_judge/judge_cache.json"),
            batch_size=args.batch_size, prompt_token_budget=args.prompt_token_budget,
        )
        print(f"LLM: {config.describe()} | prompt {PROMPT_VERSION} | context {args.context} | "
              f"batches <= {args.batch_size} candidates / ~{args.prompt_token_budget} prompt tokens"
              + (f" | <= {args.llm_rpm} requests/min" if args.llm_rpm else ""))

    def score_all(cands, feats, graph) -> dict[str, list[float]]:
        scores = {"relaxed": [relaxed_rule_score(c, feats) for c in cands]}
        for seed in range(RANDOM_SEEDS):
            rng = random.Random(seed)
            scores[f"random#{seed}"] = [rng.random() for _ in cands]
        if judge is not None:
            ctx = {}
            keys = []
            for c in cands:
                k, text = candidate_context(c, feats, graph, args.context, max_nodes=args.max_nodes)
                ctx[k] = text
                keys.append(k)
            probs = judge.judge(ctx)
            # A candidate the model never answered for gets a neutral score
            # rather than being dropped (which would change the pool).
            scores["llm"] = [probs[k] if probs[k] is not None else 0.5 for k in keys]
        return scores

    # Clean circuits: the false-alarm reference.
    clean_max: dict[str, dict[str, float]] = {}
    for circ in circuits:
        _, _, pr, _, _ = analyse(Path(f"benchmarks/{circ}.v"), args.toggle_patterns, True)
        feats = {p["gate_id"]: p["features"] for p in pr.gate_profiles}
        cands = generate_candidates(pr.gate_profiles, pr.graph)
        sc = score_all(cands, feats, pr.graph)
        clean_max[circ] = {m: max(v) if v else float("-inf") for m, v in sc.items()}
        print(f"{circ} clean: {len(cands)} candidates"
              + (f", judge calls so far {judge.calls}" if judge else ""))

    rows: list[dict] = []
    for circ in circuits:
        for shape in args.shapes:
            base = Path(SHAPES[shape])
            for strategy in STRATEGIES:
                vdirs = sorted((base / f"{circ}__{strategy}").glob("trojan_*"))[: args.variants]
                for vd in vdirs:
                    meta = json.loads((vd / "metadata.json").read_text(encoding="utf-8"))
                    _, _, pr, _, _ = analyse(vd / "circuit.v", args.toggle_patterns, True)
                    feats = {p["gate_id"]: p["features"] for p in pr.gate_profiles}
                    cands = generate_candidates(pr.gate_profiles, pr.graph)
                    truth = [i for i, c in enumerate(cands) if is_trojan_candidate(c, meta)]
                    sc = score_all(cands, feats, pr.graph)
                    n = len(cands)
                    for method, vals in sc.items():
                        row = {"circuit": circ, "shape": shape, "strategy": strategy,
                               "variant": vd.name, "method": method, "candidates": n,
                               "in_pool": int(bool(truth))}
                        if truth:
                            r = min(rank_of(vals, i) for i in truth)
                            row.update({
                                "rank": r, "top1": int(r <= 1), "top3": int(r <= 3),
                                "top5": int(r <= 5), "rr": 1.0 / r,
                                "auroc": (n - r) / (n - 1) if n > 1 else 1.0,
                            })
                        top = max(vals) if vals else float("-inf")
                        row["detect_zero_fa"] = int(top > max(clean_max[c][method] for c in circuits))
                        row["detect_vs_own_clean"] = int(top > clean_max[circ][method])
                        rows.append(row)
                    llm_rank = next((r["rank"] for r in rows[-len(sc):] if r["method"] == "llm"), None)
                    rel_rank = next((r["rank"] for r in rows[-len(sc):] if r["method"] == "relaxed"), None)
                    print(f"  {circ} {shape:4} {strategy:9} {vd.name}: {n} cands, "
                          f"relaxed rank {rel_rank}"
                          + (f", llm rank {llm_rank} (calls {judge.calls})" if judge else ""))
                    _write(out / "per_variant.csv", rows)

    _summarise(rows, out, args, model)
    if judge is not None:
        print(f"LLM calls: {judge.calls}, unanswered candidates: {judge.failed}, "
              f"tokens: {judge.prompt_tokens} prompt + {judge.completion_tokens} completion")
    return 0


def _write(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _summarise(rows: list[dict], out: Path, args, model: str) -> None:
    def agg(sel, key):
        vals = [r[key] for r in sel if key in r]
        return statistics.mean(vals) if vals else None

    table = []
    for shape in args.shapes:
        for method in ["relaxed", "random", "llm"]:
            sel = [r for r in rows if r["shape"] == shape and
                   (r["method"] == method or (method == "random" and r["method"].startswith("random#")))]
            if not sel:
                continue
            table.append({
                "shape": shape, "method": method, "variants": len({(r["circuit"], r["variant"], r["strategy"]) for r in sel}),
                "top1": agg(sel, "top1"), "top3": agg(sel, "top3"), "top5": agg(sel, "top5"),
                "mrr": agg(sel, "rr"), "auroc": agg(sel, "auroc"), "median_rank": statistics.median([r["rank"] for r in sel if "rank" in r]) if any("rank" in r for r in sel) else None,
                "detect_zero_fa": agg(sel, "detect_zero_fa"), "detect_vs_own_clean": agg(sel, "detect_vs_own_clean"),
            })
    _write(out / "summary.csv", table)

    def pct(v):
        return "-" if v is None else f"{100 * v:.0f}%"

    lines = [
        f"# LLM trojan-candidate judge -- {args.split} split",
        "",
        f"Circuits: {', '.join(args.circuits or SPLITS[args.split])}. Variants per strategy: {args.variants}. "
        f"LLM context: {args.context} (max {args.max_nodes} nodes). Model: {model}, prompt {PROMPT_VERSION}.",
        "Ranks are among all candidates of the circuit (ties at the mean). Random = mean of "
        f"{RANDOM_SEEDS} seeds. Detection @0 FA: highest candidate score above every clean circuit's.",
        "",
        "| Payload | Method | Variants | Top-1 | Top-3 | Top-5 | MRR | AUROC | Median rank | Detect @0 FA | Detect vs own clean |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in table:
        lines.append(
            f"| {t['shape']} | {t['method']} | {t['variants']} | {pct(t['top1'])} | {pct(t['top3'])} | "
            f"{pct(t['top5'])} | {t['mrr']:.3f} | {t['auroc']:.3f} | {t['median_rank']} | "
            f"{pct(t['detect_zero_fa'])} | {pct(t['detect_vs_own_clean'])} |"
        )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nWrote {out / 'per_variant.csv'}, {out / 'summary.csv'}, {out / 'summary.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
