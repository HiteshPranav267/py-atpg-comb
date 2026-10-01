"""Fit the structural-facts baseline on the dev circuits and freeze it.

Writes results/trojan_llm_judge/feature_baseline.json with one model per
held-out dev circuit ("without:c432" is trained on the other dev circuits
and is the only model c432 is ever scored with) and one on all dev
circuits ("all"), which is the model the test split uses. Test circuits
are never read.

  python scripts/train_trojan_feature_baseline.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_trojan_llm_judge import SHAPES, SPLITS, STRATEGIES  # noqa: E402
from eval_trojan_verdict import analyse  # noqa: E402
from pyatpg.evaluation.trojan_candidates import (  # noqa: E402
    candidate_facts, circuit_stats, generate_candidates, is_trojan_candidate, relaxed_rule_score)
from pyatpg.evaluation.trojan_feature_baseline import FEATURES, feature_vector, train  # noqa: E402


def rows_for(circ: str, variants: int, shapes: list[str]) -> tuple[list[list[float]], list[int]]:
    X, y = [], []
    paths = [(Path(f"benchmarks/{circ}.v"), None)]
    for shape in shapes:
        for strategy in STRATEGIES:
            for vd in sorted((Path(SHAPES[shape]) / f"{circ}__{strategy}").glob("trojan_*"))[:variants]:
                paths.append((vd / "circuit.v", json.loads((vd / "metadata.json").read_text(encoding="utf-8"))))
    for path, meta in paths:
        _, _, pr, _, _ = analyse(path, 3000, True)
        feats = {p["gate_id"]: p["features"] for p in pr.gate_profiles}
        stats = circuit_stats(feats, pr.graph)
        for c in generate_candidates(pr.gate_profiles, pr.graph):
            X.append(feature_vector(candidate_facts(c, feats, pr.graph, stats), relaxed_rule_score(c, feats)))
            y.append(int(bool(meta) and is_trojan_candidate(c, meta)))
    return X, y


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", type=int, default=10, help="training variants per strategy and shape")
    ap.add_argument("--shapes", nargs="+", default=["xor", "or", "mux"])
    ap.add_argument("--out", type=Path, default=Path("results/trojan_llm_judge/feature_baseline.json"))
    args = ap.parse_args()

    dev = SPLITS["dev"]  # the test circuits are never touched here
    data = {}
    for circ in dev:
        t0 = time.time()
        data[circ] = rows_for(circ, args.variants, args.shapes)
        print(f"{circ}: {len(data[circ][0])} candidates, {sum(data[circ][1])} trojans ({time.time() - t0:.0f}s)")

    models = {}
    for name, circs in [("all", dev)] + [(f"without:{c}", [x for x in dev if x != c]) for c in dev]:
        X = [r for c in circs for r in data[c][0]]
        y = [t for c in circs for t in data[c][1]]
        t0 = time.time()
        models[name] = {**train(X, y), "circuits": circs, "variants": args.variants, "shapes": args.shapes}
        print(f"trained {name} on {circs} in {time.time() - t0:.0f}s")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(models, indent=1), encoding="utf-8")
    w = models["all"]["weights"]
    print("standardised weights (all dev):")
    for f, v in sorted(zip(FEATURES, w), key=lambda t: -abs(t[1])):
        print(f"  {f:34} {v:+.2f}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
