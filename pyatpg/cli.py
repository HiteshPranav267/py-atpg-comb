import argparse
import sys
import json
from pyatpg.pipeline import PyATPGPipeline
from pyatpg.pipeline.phase4_validation import Validator # For print_report reuse

def print_report(report):
    """Print a formatted security audit report to the terminal."""
    trojans = report.get('trojan_candidates', [])
    cleared = report.get('cleared_gates', [])
    unknown = report.get('unknown_gates', [])
    comparison = report.get('comparison', {})

    print("\n" + "=" * 60)
    print("  PYATPG (ATALANTA) SECURITY AUDIT REPORT")
    print("=" * 60)

    if comparison:
        print(f"\n  Coverage (Targeted): {comparison.get('targeted_coverage', '--')}%")
        print(f"  Coverage (Baseline): {comparison.get('baseline_coverage', '--')}%")
        diff = comparison.get('coverage_diff')
        if diff is not None:
            arrow = "▲" if diff >= 0 else "▼"
            print(f"  Delta:               {arrow} {abs(diff)}%")
        patterns = comparison.get('pattern_reduction_pct')
        if patterns is not None:
            print(f"  Pattern Reduction:   {patterns}% fewer vectors")
        speedup = comparison.get('speedup_factor')
        if speedup is not None:
            print(f"  Speedup Factor:      {speedup}x")

    print(f"\n  {'─' * 56}")
    print(f"  TROJAN CANDIDATES: {len(trojans)}")
    print(f"  {'─' * 56}")
    for t in trojans:
        print(f"  ⛔  {t['gate_id']:20s}  →  {t['reason']}")

    print(f"\n  {'─' * 56}")
    print(f"  CLEARED GATES:     {len(cleared)}")
    print(f"  {'─' * 56}")
    for c in cleared:
        print(f"  ✅  {c['gate_id']:20s}  →  {c['reason']}")

    if unknown:
        print(f"\n  {'─' * 56}")
        print(f"  NO DATA:           {len(unknown)}")
        print(f"  {'─' * 56}")
        for u in unknown:
            print(f"  ⚠️   {u['gate_id']:20s}  →  {u['reason']}")

    print("\n" + "=" * 60)
    if trojans:
        print(f"  🚨 VERDICT: {len(trojans)} HARDWARE TROJAN CANDIDATE(S) IDENTIFIED")
    else:
        print("  ✅ VERDICT: NO HARDWARE TROJANS DETECTED")
    print("=" * 60 + "\n")

def main():
    parser = argparse.ArgumentParser(description="PyATPG Full Pipeline (Atalanta Local Version)")
    parser.add_argument("netlist", help="Path to the .v or .bench netlist file")
    parser.add_argument("--api-key", help="Gemini API Key (offline heuristic if omitted)")
    parser.add_argument("--patterns", type=int, default=3000,
                        help="Number of toggle-rate simulation patterns")
    parser.add_argument("--output", "-o", default="security_report.json",
                        help="Path to save the final JSON report")
    args = parser.parse_args()

    pipeline = PyATPGPipeline(api_key=args.api_key)
    
    try:
        report = pipeline.run(args.netlist, patterns=args.patterns)
    except Exception as e:
        print(f"❌ Pipeline Execution Failed: {str(e)}")
        sys.exit(1)
        
    print_report(report)
    
    with open(args.output, 'w') as f:
        json.dump(report, f, indent=2, default=str)
        
    print(f"✅ Full Pipeline Complete. Report saved to {args.output}")

if __name__ == "__main__":
    main()
