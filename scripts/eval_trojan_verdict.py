"""How well does the structural trojan verdict separate infected from clean circuits?

For each circuit:

1. Generate trojan-inserted variants with the project's own generator, for
   every insertion strategy (random, rare_node, low_scoap).
2. Run the same structural detector + verdict the ATPG page shows on the
   clean circuit and on every variant (full-scan model for circuits with
   flip-flops, as the app does by default).
3. Score it:
     * detection rate  -- infected variants with verdict "trojan_suspected"
     * false-alarm rate -- clean circuits with verdict "trojan_suspected"
     * localization    -- infected variants where a flagged gate is one of the
                          trojan's own nets (trigger output / payload target)
   and optionally (--llm, --targeting) whether gate *targeting* puts a
   trojan net into the targeted fault list: LLM vs structural heuristic vs
   random at the same K.

Outputs (in --out): per_variant.csv, summary.csv, summary.md.

Examples:
    python scripts/eval_trojan_verdict.py benchmarks/c432.v benchmarks/c880.v --variants 10
    python scripts/eval_trojan_verdict.py benchmarks/c2670.v --variants 10 --targeting --llm
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyatpg.api.services.trojan_service import TrojanService  # noqa: E402
from pyatpg.atpg.structural import parse_circuit, profile_circuit  # noqa: E402
from pyatpg.atpg.suspects import find_structural_suspects  # noqa: E402
from pyatpg.atpg.targeting import FaultTargetSelector  # noqa: E402
from pyatpg.atpg.verdict import build_verdict  # noqa: E402
from pyatpg.core.scan import to_full_scan  # noqa: E402
from pyatpg.evaluation.trojan_detection import trojan_footprint  # noqa: E402

STRATEGIES = ("random", "rare_node", "low_scoap")


def analyse(path: Path, toggle_patterns: int, full_scan: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_circuit(text, path.name)
    if parsed.circuit.flipflops and full_scan:
        parsed.circuit, _ = to_full_scan(parsed.circuit)
        parsed.derived = True
    profile = profile_circuit(parsed, toggle_patterns=toggle_patterns)
    try:
        suspects, reasons = find_structural_suspects(profile.gate_profiles, profile.graph)
    except Exception:  # noqa: BLE001 - count a detector crash as "nothing flagged"
        suspects, reasons = [], []
    verdict = build_verdict(suspects, reasons, [])
    return text, parsed, profile, suspects, verdict.verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("circuits", nargs="+", type=Path)
    ap.add_argument("--variants", type=int, default=10, help="trojan variants per strategy")
    ap.add_argument("--strategies", nargs="+", default=list(STRATEGIES), choices=STRATEGIES)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=Path("results/trojan_verdict"))
    ap.add_argument("--toggle-patterns", type=int, default=3000)
    ap.add_argument("--no-full-scan", action="store_true")
    ap.add_argument("--targeting", action="store_true",
                    help="also score whether gate targeting includes a trojan net")
    ap.add_argument("--llm", action="store_true", help="with --targeting: include LLM targeting")
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
        os.environ["LLM_RESULT_CACHE"] = "0"
        print(f"LLM: {config.describe()}")

    dataset_root = args.out / "datasets"
    args.out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for circuit in args.circuits:
        t0 = time.monotonic()
        _, _, _, clean_suspects, clean_verdict = analyse(circuit, args.toggle_patterns, full_scan)
        rows.append({"circuit": circuit.stem, "strategy": "clean", "variant": "clean",
                     "infected": 0, "verdict": clean_verdict, "flagged": len(clean_suspects),
                     "localized": "", "footprint": ""})
        print(f"\n{circuit.name}: clean verdict = {clean_verdict} ({len(clean_suspects)} flagged)")

        source = circuit.read_text(encoding="utf-8", errors="replace")
        for strategy in args.strategies:
            name = f"{circuit.stem}__{strategy}"
            TrojanService.generate_dataset(
                source_text=source, circuit_name=name, variants=args.variants,
                strategy=strategy, filename=circuit.name, output_dir=str(dataset_root),
                seed=args.seed,
            )
            for vdir in sorted((dataset_root / name).glob("trojan_*")):
                meta = json.loads((vdir / "metadata.json").read_text(encoding="utf-8"))
                footprint = trojan_footprint(meta)
                text, parsed, profile, suspects, verdict = analyse(
                    vdir / "circuit.v", args.toggle_patterns, full_scan
                )
                row = {"circuit": circuit.stem, "strategy": strategy, "variant": vdir.name,
                       "infected": 1, "verdict": verdict, "flagged": len(suspects),
                       "localized": int(bool(set(suspects) & footprint)),
                       "footprint": " ".join(sorted(footprint))}

                if args.targeting:
                    structural = FaultTargetSelector().select(profile.gate_profiles, graph=None)
                    k = len(structural.gates)
                    row["k"] = k
                    row["target_structural"] = int(bool(set(structural.gates) & footprint))
                    universe = [p["gate_id"] for p in profile.gate_profiles
                                if p["features"].get("node_type") != "PI"]
                    rnd = random.Random(args.seed).sample(universe, min(k, len(universe)))
                    row["target_random"] = int(bool(set(rnd) & footprint))
                    if provider is not None:
                        try:
                            # graph=None: judge the model alone, without the
                            # structural detector's force-included suspects.
                            sel = FaultTargetSelector(provider=provider, max_gates=k).select(
                                profile.gate_profiles, graph=None, source_text=text
                            )
                            row["target_llm"] = int(bool(set(sel.gates) & footprint))
                        except Exception as exc:  # noqa: BLE001
                            print(f"    llm targeting failed on {vdir.name}: {exc}")
                            row["target_llm"] = ""
                rows.append(row)
                print(f"  {strategy:10} {vdir.name}: {verdict:22} flagged {len(suspects):3} "
                      f"localized {row['localized']}"
                      + (f"  targeting S/R/L {row.get('target_structural')}/{row.get('target_random')}/"
                         f"{row.get('target_llm', '-')}" if args.targeting else ""))
        print(f"  ({time.monotonic() - t0:.1f}s)")

    fields = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k)
                    if k in rows[0] else 99)
    with open(args.out / "per_variant.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    def rate(sel, key, value=1):
        vals = [r.get(key) for r in sel if r.get(key) not in ("", None)]
        return (sum(1 for v in vals if v == value) / len(vals)) if vals else None

    summary = []
    for circuit in dict.fromkeys(r["circuit"] for r in rows):
        infected = [r for r in rows if r["circuit"] == circuit and r["infected"]]
        clean = [r for r in rows if r["circuit"] == circuit and not r["infected"]]
        for strategy in ["all", *args.strategies]:
            sel = infected if strategy == "all" else [r for r in infected if r["strategy"] == strategy]
            if not sel:
                continue
            summary.append({
                "circuit": circuit, "strategy": strategy, "variants": len(sel),
                "detection_rate": rate(sel, "verdict", "trojan_suspected"),
                "false_alarm_on_clean": rate(clean, "verdict", "trojan_suspected"),
                "localization_rate": rate(sel, "localized"),
                "target_hit_structural": rate(sel, "target_structural"),
                "target_hit_random": rate(sel, "target_random"),
                "target_hit_llm": rate(sel, "target_llm"),
            })
    with open(args.out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)

    def pct(v):
        return "-" if v is None else f"{100 * v:.0f}%"

    lines = [
        "# Structural trojan verdict: infected vs clean",
        "",
        "Detection = verdict \"trojan_suspected\" on an infected variant. False alarm = the same "
        "verdict on the clean circuit. Localized = a flagged gate is one of the trojan's own nets. "
        "Target hit = the targeted fault list includes a trojan net (same K for every method).",
        "",
        "| Circuit | Strategy | Variants | Detection | False alarm (clean) | Localized "
        "| Target hit: structural | random | LLM |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(
            f"| {s['circuit']} | {s['strategy']} | {s['variants']} | {pct(s['detection_rate'])} "
            f"| {pct(s['false_alarm_on_clean'])} | {pct(s['localization_rate'])} "
            f"| {pct(s['target_hit_structural'])} | {pct(s['target_hit_random'])} "
            f"| {pct(s['target_hit_llm'])} |"
        )
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nWrote {args.out / 'per_variant.csv'}, {args.out / 'summary.csv'}, {args.out / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
