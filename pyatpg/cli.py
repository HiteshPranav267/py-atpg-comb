"""Command-line entry point for running an Atalanta ATPG analysis."""
import argparse
import json
import sys
from dataclasses import asdict

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService

#: Printed in place of any metric Atalanta did not report.
UNREPORTED = "Not reported by Atalanta"


def _fmt(value, suffix=""):
    return UNREPORTED if value is None else f"{value}{suffix}"


def _print_run(run: dict) -> None:
    results = run["results"]
    timing = run["timing"]
    circuit = run["circuit"]

    print(f"\n  {'-' * 62}")
    print(f"  {run['label']}")
    print(f"  {'-' * 62}")
    print(f"    Circuit (as read by Atalanta) : {_fmt(circuit['name'])}")
    print(f"      Primary inputs / outputs    : {_fmt(circuit['primary_inputs'])} / {_fmt(circuit['primary_outputs'])}")
    print(f"      Gates / logic depth         : {_fmt(circuit['gates'])} / {_fmt(circuit['logic_depth'])}")
    print(f"    Fault coverage                : {_fmt(results['fault_coverage'], ' %')}")
    print(f"    Patterns before compaction    : {_fmt(results['patterns_before_compaction'])}")
    print(f"    Patterns after compaction     : {_fmt(results['patterns_after_compaction'])}")
    print(f"    Collapsed faults              : {_fmt(results['collapsed_faults'])}")
    print(f"    Detected faults               : {_fmt(results['detected_faults'])}")
    print(f"    Redundant faults              : {_fmt(results['redundant_faults'])}")
    print(f"    Aborted faults                : {_fmt(results['aborted_faults'])}")
    print(f"    Backtrackings                 : {_fmt(results['backtrackings'])}")
    print(f"    Memory used                   : {_fmt(run['memory_mb'], ' MB')}")
    print(f"    CPU total                     : {_fmt(timing['total'], ' s')}")
    print(f"    Test vectors generated        : {run['test_vectors']['count']}")
    print(f"    Undetected faults listed      : {len(run['faults'])}")


def print_report(analysis: dict) -> None:
    print("\n" + "=" * 66)
    print("  ATALANTA ATPG ANALYSIS")
    print("=" * 66)
    print(f"  Mode    : {analysis['mode_label']}")
    print(f"  Job id  : {analysis['job_id']}")

    circuit = analysis["circuit"]
    print(f"\n  Circuit : {circuit['name']} ({circuit['format']})")
    print(f"    Inputs {circuit['primary_inputs']} | Outputs {circuit['primary_outputs']} "
          f"| Gates {circuit['gates']} | Depth {circuit['logic_depth']}")

    for run in analysis["runs"].values():
        _print_run(run)

    comparison = analysis.get("comparison")
    if comparison:
        print(f"\n  {'-' * 62}")
        print("  Comparison")
        print(f"  {'-' * 62}")
        print(f"    Targeted coverage      : {_fmt(comparison['targeted_coverage'], ' %')}")
        print(f"    Non-targeted coverage  : {_fmt(comparison['non_targeted_coverage'], ' %')}")
        print(f"    Coverage delta         : {_fmt(comparison['coverage_delta'], ' pts')}")
        print(f"    Pattern reduction      : {_fmt(comparison['pattern_reduction_pct'], ' %')}")
        for note in comparison["notes"]:
            print(f"    Note: {note}")

    circuit_analysis = analysis.get("circuit_analysis")
    if circuit_analysis:
        origin = "language model" if circuit_analysis["source"] == "llm" else "computed structural metrics"
        print(f"\n  {'-' * 62}")
        print(f"  Circuit analysis (from {origin})")
        print(f"  {'-' * 62}")
        if circuit_analysis["architecture_summary"]:
            print(f"    {circuit_analysis['architecture_summary']}")
        for observation in circuit_analysis["observations"]:
            gates = ", ".join(observation["gates"][:8]) or "-"
            print(f"\n    * {observation['title']}")
            print(f"      {observation['detail']}")
            print(f"      Gates: {gates}")

    correlation = analysis.get("correlation")
    if correlation:
        print(f"\n  {'-' * 62}")
        print("  Correlated insights")
        print(f"  {'-' * 62}")
        print(f"    {correlation['summary']}")
        for item in correlation["correlated"]:
            print(f"    * {item['statement']}")

    print("\n" + "=" * 66 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Run an Atalanta ATPG analysis over a combinational netlist."
    )
    parser.add_argument("netlist", help="Path to the .v, .verilog or .bench netlist")
    parser.add_argument(
        "--mode",
        choices=[m.value for m in AnalysisMode],
        default=AnalysisMode.COMPARE.value,
        help="Which run(s) to perform (default: compare)",
    )
    parser.add_argument("--api-key", help="Gemini API key; structural analysis is used without one")
    parser.add_argument(
        "--patterns",
        type=int,
        default=3000,
        help="Random patterns used for toggle-rate profiling (does not affect Atalanta)",
    )
    parser.add_argument("--output", "-o", default="atpg_report.json", help="Where to write the JSON report")
    args = parser.parse_args()

    try:
        with open(args.netlist, "r", encoding="utf-8") as handle:
            source_text = handle.read()
    except OSError as exc:
        print(f"Could not read {args.netlist}: {exc}")
        sys.exit(1)

    service = AtpgAnalysisService(api_key=args.api_key)
    try:
        analysis = service.run(
            source_text=source_text,
            filename=args.netlist,
            mode=AnalysisMode(args.mode),
            toggle_patterns=args.patterns,
            use_llm=bool(args.api_key),
        )
    except Exception as exc:  # noqa: BLE001 - surface the real reason and stop
        print(f"Analysis failed: {exc}")
        sys.exit(1)

    payload = analysis.to_dict()
    print_report(payload)

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    print(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
