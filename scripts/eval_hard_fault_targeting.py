"""Does LLM fault targeting find the hard faults better than simple baselines?

For each circuit:

1. Run whole-circuit ATPG (Atalanta, full-scan model for circuits with
   flip-flops). Its redundant and aborted faults are the ground truth for
   "hard": a gate is *hard* if a fault on its output net is redundant or
   aborted. This is measured, never guessed.
2. Pick K gates with each method -- the same K for all, so they compete on
   equal terms:
     * llm         -- the app's FaultTargetSelector with a model (repeatable)
     * structural  -- the same selector's non-LLM heuristic
     * scoap       -- highest CC0 + CC1 + CO (hardest to control and observe)
     * rare        -- lowest toggle rate
     * reconv      -- most reconvergent fanout stems (no LLM)
     * random      -- uniform, averaged over several seeds
3. Score each pick: hits (picked gates that are hard), precision, recall,
   chance level (hard gates / all gates), and lift = precision / chance.
   Lift 1.0 means "no better than random".

Outputs (in --out): per_run.csv (every method x repeat), summary.csv and
summary.md (mean and spread per circuit x method).

Examples:
    python scripts/eval_hard_fault_targeting.py benchmarks/s5378.v benchmarks/s13207.v
    python scripts/eval_hard_fault_targeting.py benchmarks/s*.v --llm --llm-repeats 3
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService  # noqa: E402
from pyatpg.atpg.structural import parse_circuit, profile_circuit  # noqa: E402
from pyatpg.atpg.targeting import FaultTargetSelector  # noqa: E402
from pyatpg.core.scan import to_full_scan  # noqa: E402

HARD_STATUSES = {"redundant", "aborted"}


def load_circuit(path: Path, full_scan: bool, toggle_patterns: int):
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_circuit(text, path.name)
    if parsed.circuit.flipflops and full_scan:
        parsed.circuit, _ = to_full_scan(parsed.circuit)
        parsed.derived = True
    profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
    return text, parsed, profile


def hard_gates(text: str, name: str, full_scan: bool, backtrack_limit: int | None):
    """Gates with a redundant/aborted fault on their output, from a full ATPG run."""
    analysis = AtpgAnalysisService().run(
        source_text=text,
        filename=name,
        mode=AnalysisMode.NON_TARGETED,
        use_llm=False,
        full_scan=full_scan,
        toggle_patterns=50,
        backtrack_limit=backtrack_limit,
    )
    run = next(iter(analysis.runs.values()))
    hard = {f.gate.split("->")[0] for f in run.faults if f.status in HARD_STATUSES}
    return hard, run.results


def score(picked: list[str], hard: set[str], universe_size: int) -> dict:
    hits = len(set(picked) & hard)
    k = len(picked)
    chance = len(hard) / universe_size if universe_size else 0.0
    precision = hits / k if k else 0.0
    return {
        "k": k,
        "hits": hits,
        "precision": precision,
        "recall": hits / len(hard) if hard else 0.0,
        "chance": chance,
        "lift": precision / chance if chance else float("nan"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("circuits", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("results/hard_fault_targeting"))
    ap.add_argument("--llm", action="store_true", help="also evaluate LLM targeting (uses .env provider)")
    ap.add_argument("--llm-repeats", type=int, default=3, help="fresh LLM selections per circuit")
    ap.add_argument("--random-seeds", type=int, default=10)
    ap.add_argument("--k", type=int, default=None,
                    help="gates to pick per method (default: the selector's own K)")
    ap.add_argument("--no-full-scan", action="store_true",
                    help="skip circuits with flip-flops instead of using the full-scan model")
    ap.add_argument("--toggle-patterns", type=int, default=3000)
    ap.add_argument("--backtrack-limit", type=int, default=None,
                    help="Atalanta -b for the ground-truth run (higher = fewer aborts)")
    args = ap.parse_args()

    full_scan = not args.no_full_scan
    provider = None
    if args.llm:
        from pyatpg.ranking.providers import create_provider, resolve_llm_config

        config = resolve_llm_config()
        if config is None:
            print("--llm given but no LLM key is configured (.env / environment).", file=sys.stderr)
            return 2
        provider = create_provider(config)
        if args.llm_repeats > 1:
            # Each repeat must be a fresh model call, not a cache hit.
            os.environ["LLM_RESULT_CACHE"] = "0"
        print(f"LLM: {config.describe()}")

    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for path in args.circuits:
        t0 = time.monotonic()
        text, parsed, profile = load_circuit(path, full_scan, args.toggle_patterns)
        if parsed.circuit.flipflops:
            print(f"skip {path.name}: has flip-flops and --no-full-scan was given")
            continue
        profiles = [p for p in profile.gate_profiles if p["features"].get("node_type") != "PI"]
        universe = [p["gate_id"] for p in profiles]
        hard, results = hard_gates(text, path.name, full_scan, args.backtrack_limit)
        hard &= set(universe)
        print(f"\n{path.name}: {len(universe)} gates, {len(hard)} hard "
              f"(coverage {results.fault_coverage}%, redundant {results.redundant_faults}, "
              f"aborted {results.aborted_faults})")

        structural = FaultTargetSelector().select(profile.gate_profiles, graph=profile.graph)
        k = args.k or len(structural.gates)

        def record(method: str, repeat: int, picked: list[str], seconds: float = 0.0, note: str = ""):
            row = {"circuit": path.stem, "method": method, "repeat": repeat,
                   "gates": len(universe), "hard_gates": len(hard),
                   **score(picked[:k], hard, len(universe)), "seconds": round(seconds, 3), "note": note}
            rows.append(row)
            print(f"  {method:10} #{repeat}: hits {row['hits']}/{row['k']}  "
                  f"precision {row['precision']:.3f}  recall {row['recall']:.3f}  lift {row['lift']:.2f}")

        record("structural", 0, structural.gates)
        feats = {p["gate_id"]: p["features"] for p in profiles}
        scoap = sorted(universe, key=lambda g: -(feats[g].get("cc0", 0) + feats[g].get("cc1", 0)
                                                 + feats[g].get("co", 0)))
        record("scoap", 0, scoap)
        rare = sorted(universe, key=lambda g: feats[g].get("toggle_rate", 1.0))
        record("rare", 0, rare)
        reconv = sorted(universe, key=lambda g: -feats[g].get("reconvergence", 0))
        record("reconv", 0, reconv)
        for seed in range(args.random_seeds):
            record("random", seed, random.Random(seed).sample(universe, min(k, len(universe))))

        if provider is not None:
            for rep in range(args.llm_repeats):
                start = time.monotonic()
                selector = FaultTargetSelector(provider=provider, max_gates=k)
                try:
                    sel = selector.select(profile.gate_profiles, graph=profile.graph,
                                          source_text=parsed.source_text)
                    note = sel.strategy if sel.used_llm else "fell back to structural"
                    record("llm", rep, sel.gates, time.monotonic() - start, note)
                except Exception as exc:  # noqa: BLE001 - report and keep going
                    print(f"  llm #{rep}: failed ({exc})")
        print(f"  ({time.monotonic() - t0:.1f}s)")

    if not rows:
        print("No circuits evaluated.")
        return 1

    fields = list(rows[0].keys())
    with open(args.out / "per_run.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = []
    for circuit in dict.fromkeys(r["circuit"] for r in rows):
        for method in dict.fromkeys(r["method"] for r in rows):
            sel = [r for r in rows if r["circuit"] == circuit and r["method"] == method]
            if not sel:
                continue

            def stat(key):
                vals = [r[key] for r in sel]
                return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)

            p, p_sd = stat("precision")
            rc, _ = stat("recall")
            lift, lift_sd = stat("lift")
            hits, _ = stat("hits")
            summary.append({
                "circuit": circuit, "method": method, "runs": len(sel), "k": sel[0]["k"],
                "hard_gates": sel[0]["hard_gates"], "gates": sel[0]["gates"],
                "chance": round(sel[0]["chance"], 4), "hits_mean": round(hits, 2),
                "precision_mean": round(p, 4), "precision_sd": round(p_sd, 4),
                "recall_mean": round(rc, 4), "lift_mean": round(lift, 3), "lift_sd": round(lift_sd, 3),
            })
    with open(args.out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)

    lines = [
        "# Hard-fault targeting: LLM vs baselines",
        "",
        "Hard gate = a gate with a redundant or aborted fault on its output in a whole-circuit "
        "Atalanta run. Lift = precision / chance (1.0 = no better than random).",
        "",
        "| Circuit | Method | Runs | K | Hard / gates | Hits (mean) | Precision | Recall | Lift |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary:
        sd = f" ± {s['precision_sd']:.3f}" if s["runs"] > 1 else ""
        lines.append(
            f"| {s['circuit']} | {s['method']} | {s['runs']} | {s['k']} | {s['hard_gates']} / {s['gates']} "
            f"| {s['hits_mean']} | {s['precision_mean']:.3f}{sd} | {s['recall_mean']:.3f} | {s['lift_mean']:.2f} |"
        )
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nWrote {args.out / 'per_run.csv'}, {args.out / 'summary.csv'}, {args.out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
