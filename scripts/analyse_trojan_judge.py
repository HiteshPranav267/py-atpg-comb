"""Per-candidate view of a finished LLM-judge run, read from the judge cache.

No LLM calls: candidates and contexts are rebuilt deterministically and the
judge's answers are looked up in results/trojan_llm_judge/judge_cache.json.
Writes a per-candidate CSV and a markdown page of the interesting cases
(true trojans the LLM ranked low, ordinary candidates it ranked high), with
the LLM's "why" and the context it saw. Dev circuits only, by design.

  python scripts/analyse_trojan_judge.py --version v2 --out results/trojan_llm_judge/dev_B_v2
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_trojan_llm_judge import SHAPES, SPLITS, STRATEGIES, rank_of  # noqa: E402
from eval_trojan_verdict import analyse  # noqa: E402
from pyatpg.evaluation.trojan_candidates import (  # noqa: E402
    candidate_context, generate_candidates, is_trojan_candidate, relaxed_rule_score)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--context", default="B")
    ap.add_argument("--max-nodes", type=int, default=14)
    ap.add_argument("--variants", type=int, default=2)
    ap.add_argument("--shapes", nargs="+", default=["xor", "or", "mux"])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    circuits = SPLITS["dev"]  # never the test split
    cache = json.loads(Path("results/trojan_llm_judge/judge_cache.json").read_text(encoding="utf-8"))

    def judged(key):
        return cache.get(f"{args.model}|{args.version}|{key}") or {}

    rows, cases = [], []

    def run(label, circ, shape, strategy, path, meta):
        _, _, pr, _, _ = analyse(path, 3000, True)
        feats = {p["gate_id"]: p["features"] for p in pr.gate_profiles}
        cands = generate_candidates(pr.gate_profiles, pr.graph)
        ctx = [candidate_context(c, feats, pr.graph, args.context, max_nodes=args.max_nodes) for c in cands]
        llm = [judged(k).get("p") for k, _ in ctx]
        llm_s = [p if p is not None else 0.5 for p in llm]
        rel = [relaxed_rule_score(c, feats) for c in cands]
        truth = [bool(meta) and is_trojan_candidate(c, meta) for c in cands]
        for i, c in enumerate(cands):
            r = {"case": label, "circuit": circ, "shape": shape, "strategy": strategy, "truth": int(truth[i]),
                 "trig_toggle": feats.get(c.trigger, {}).get("toggle_rate"),
                 "other_toggle": feats.get(c.other, {}).get("toggle_rate"),
                 "payload_type": feats.get(c.payload, {}).get("gate_type"),
                 "llm_p": llm[i], "llm_rank": rank_of(llm_s, i), "relaxed": round(rel[i], 3),
                 "relaxed_rank": rank_of(rel, i), "n": len(cands), "why": judged(ctx[i][0]).get("why", ""),
                 "context": ctx[i][1]}
            rows.append(r)
        return cands

    for circ in circuits:
        run(f"{circ}/clean", circ, "clean", "-", Path(f"benchmarks/{circ}.v"), None)
        for shape in args.shapes:
            for strategy in STRATEGIES:
                for vd in sorted((Path(SHAPES[shape]) / f"{circ}__{strategy}").glob("trojan_*"))[: args.variants]:
                    meta = json.loads((vd / "metadata.json").read_text(encoding="utf-8"))
                    run(f"{circ}/{shape}/{strategy}/{vd.name}", circ, shape, strategy, vd / "circuit.v", meta)

    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "per_candidate.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[k for k in rows[0] if k != "context"])
        w.writeheader()
        w.writerows({k: v for k, v in r.items() if k != "context"} for r in rows)

    md = [f"# Judge {args.version} on dev: trojans and top false positives", ""]
    md.append("| case | truth | trig toggle | other toggle | payload | LLM p | LLM rank | relaxed rank | n | LLM why |")
    md.append("|---|---|---|---|---|---|---|---|---|---|")
    by_case = {}
    for r in rows:
        by_case.setdefault(r["case"], []).append(r)
    detail = []
    for case, rs in by_case.items():
        show = [r for r in rs if r["truth"]]
        show += sorted((r for r in rs if not r["truth"]), key=lambda r: r["llm_rank"])[:2]
        for r in show:
            md.append(f"| {case} | {'TROJAN' if r['truth'] else 'clean'} | {r['trig_toggle']} | {r['other_toggle']} | "
                      f"{r['payload_type']} | {r['llm_p']} | {r['llm_rank']} | {r['relaxed_rank']} | {r['n']} | {r['why']} |")
            if r["truth"] and r["llm_rank"] > 3 or (not r["truth"] and r["llm_rank"] <= 1):
                detail.append(f"### {case} ({'TROJAN' if r['truth'] else 'clean'}, LLM rank {r['llm_rank']}, "
                              f"p={r['llm_p']}): {r['why']}\n```\n{r['context']}\n```")
    md += ["", "## Contexts of trojans ranked below 3 and of clean candidates ranked first", ""] + detail
    (args.out / "failures.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    missing = sum(1 for r in rows if r["llm_p"] is None)
    print(f"{len(rows)} candidate rows, {missing} without a cached {args.version} answer. Wrote {args.out}/failures.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
