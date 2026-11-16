"""The printed report of ``pyatpg run``: one section per part of the analysis.

Every function takes the analysis as the JSON-ready dict
(``AtpgAnalysis.to_dict()``), the same data the ``-o`` report file holds.
"""
from __future__ import annotations

#: Printed in place of any metric the ATPG tool did not report.
UNREPORTED = "Not reported by the ATPG tool"


def _fmt(value, suffix=""):
    return UNREPORTED if value is None else f"{value}{suffix}"


def _print_run(run: dict) -> None:
    results = run["results"]
    timing = run["timing"]
    circuit = run["circuit"]

    print(f"\n  {'-' * 62}")
    print(f"  {run['label']}")
    print(f"  {'-' * 62}")
    print(f"    Circuit (as read by the ATPG tool) : {_fmt(circuit['name'])}")
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
    pc = run.get("pattern_coverage")
    if pc:
        if pc.get("unavailable_reason"):
            print(f"    Whole-circuit pattern cov.    : not measured ({pc['unavailable_reason']})")
        else:
            print(f"    Whole-circuit pattern cov.    : {_fmt(pc['coverage'], ' %')} "
                  f"({pc['detected_faults']} of {pc['total_faults']} circuit faults, simulated)")
    attempts = run.get("attempts") or []
    if len(attempts) > 1:
        print("    Search-effort escalation      :")
        for a in attempts:
            print(f"      {a['label']:<24} coverage {_fmt(a['fault_coverage'], ' %')}, "
                  f"aborted {_fmt(a['aborted_faults'])}, {_fmt(a['cpu_seconds'], ' s')}")


def _print_sequential(sequential: dict) -> None:
    results = sequential["analysis"]["results"]
    print(f"\n  {'-' * 62}")
    print("  Sequential ATPG (time-frame-expansion engine)")
    print(f"  {'-' * 62}")
    print(f"    Fault coverage                : {_fmt(results['fault_coverage'], ' %')}")
    print(f"    Detected / total faults       : {_fmt(results['detected_faults'])} / {_fmt(results['total_faults'])}")
    print(f"    Test sequences generated      : {_fmt(results['sequence_count'])}")
    print(f"    Total clock cycles            : {_fmt(results['total_clock_cycles'])}")
    print(f"    Longest sequence (cycles)     : {_fmt(results['max_sequence_length'])}")
    usage = sequential.get("llm_usage") or {}
    if usage.get("enabled"):
        print(f"    LLM-assisted (LLM-1 + LLM-2)  : {usage.get('api_calls', 0)} call(s), "
              f"~{usage.get('total_tokens_est', 0)} tokens")
    for note in sequential["analysis"].get("notes", []):
        print(f"    Note: {note}")


#: Display names of the cone orderings, in the order they are printed.
ORDERING_LABELS = {
    "R": "R       (rarity order only)",
    "R+Rand1": "R+Rand1 (rarity + random, seed 1)",
    "R+Rand2": "R+Rand2 (rarity + random, seed 2)",
    "R+Rand3": "R+Rand3 (rarity + random, seed 3)",
    "R+S": "R+S     (rarity + structural score)",
    "R+L": "R+L     (rarity + LLM scores)",
}


#: How the verdict codes read in the report.
VERDICT_LABELS = {
    "trojan_suspected": "Trojan suspected",
    "inconclusive": "Inconclusive",
    "no_trojan_indicated": "No Trojan indicated",
}


def _print_verdict(verdict: dict | None) -> None:
    """The tool's own answer: is there Trojan-like logic, and which gates."""
    if not verdict:
        return
    print(f"\n  {'-' * 62}")
    print("  Trojan verdict")
    print(f"  {'-' * 62}")
    label = VERDICT_LABELS.get(verdict["verdict"], verdict["verdict"])
    print(f"    Verdict                       : {label} (confidence {_fmt(verdict.get('confidence'))})")
    suspects = verdict.get("suspects") or []
    strong = [s for s in suspects if s.get("confidence", 0) >= 0.7]
    print(f"    Strong suspects (>= 0.7)      : {len(strong)}"
          + (f"   weaker leads: {len(suspects) - len(strong)}" if len(suspects) > len(strong) else ""))
    if strong:
        names = ", ".join(s["gate"] for s in strong[:10])
        print(f"      {names}{', ...' if len(strong) > 10 else ''}")
    if verdict.get("summary"):
        print(f"    {verdict['summary']}")


def _print_trojan_check(truth: dict | None) -> None:
    """Known Trojan gates (found by name) against each gate list, when there are any."""
    if not truth:
        return
    print(f"\n  {'-' * 62}")
    print("  Trojan check (Trojan gates recognised by their names)")
    print(f"  {'-' * 62}")
    print(f"    Trojan gates in the netlist   : {len(truth['trojan_gates'])}")
    selection = truth.get("targeted_selection")
    if selection:
        print(f"    In the targeted fault list    : {len(selection['trojan_gates_hit'])} "
              f"of {selection['selected']} gates on the list")
    orderings = truth.get("orderings")
    if orderings:
        total = orderings["trojan_gates"]
        print("    Cone orderings at the same list size (Trojan gates in the list):")
        for key, row in orderings["lists"].items():
            first = row.get("first_trojan_cone")
            where = f"first Trojan cone: #{first} of {row['cones']}" if first else "no Trojan cone"
            print(f"      {ORDERING_LABELS.get(key, key):<38}: {row['trojan_hits']:>3} / {total}  ({where})")


def print_report(analysis: dict) -> None:
    is_sequential = analysis.get("sequential") is not None
    print("\n" + "=" * 66)
    print("  SEQUENTIAL ATPG ANALYSIS" if is_sequential else "  ATPG ANALYSIS")
    print("=" * 66)
    mode_label = "Time-frame-expansion engine (sequential)" if is_sequential else analysis["mode_label"]
    print(f"  Mode    : {mode_label}")
    print(f"  Job id  : {analysis['job_id']}")

    circuit = analysis["circuit"]
    depth = circuit.get("logic_depth", circuit.get("max_level"))
    print(f"\n  Circuit : {circuit['name']} ({circuit['format']})")
    print(f"    Inputs {circuit['primary_inputs']} | Outputs {circuit['primary_outputs']} "
          f"| Gates {circuit['gates']} | Flip-flops {circuit.get('flip_flops', 0)} | Depth {depth}")

    if is_sequential:
        _print_sequential(analysis["sequential"])
        for note in analysis.get("notes", []):
            print(f"\n  Note: {note}")
        print("\n" + "=" * 66 + "\n")
        return

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
        if comparison.get("targeted_simulated_coverage") is not None:
            # The one pair with a shared denominator: each run's patterns
            # fault-simulated over the same whole-circuit fault list.
            print(f"    Whole-circuit, same fault list ({_fmt(comparison.get('simulated_fault_count'))} faults):")
            print(f"      Targeted patterns    : {_fmt(comparison['targeted_simulated_coverage'], ' %')}")
            print(f"      Non-targeted patterns: {_fmt(comparison['non_targeted_simulated_coverage'], ' %')}")
            print(f"      Delta                : {_fmt(comparison['simulated_coverage_delta'], ' pts')}")
        for note in comparison["notes"]:
            print(f"    Note: {note}")

    usage = analysis.get("llm_usage") or {}
    if usage.get("enabled"):
        exact = "exact" if usage.get("tokens_exact") else "estimated"
        print(f"\n  LLM usage ({usage.get('provider', '?')} / {usage.get('model', '?')}): "
              f"{usage.get('api_calls', 0)} call(s), {usage.get('cached_hits', 0)} cached, "
              f"{usage.get('prompt_tokens_est', 0)} prompt + {usage.get('response_tokens_est', 0)} "
              f"response tokens ({exact})")
        for stage, row in (usage.get("by_stage") or {}).items():
            print(f"    {stage:<12}: {row.get('tokens_est', 0)} tokens, {row.get('calls', 0)} call(s)")

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

    _print_verdict(analysis.get("verdict"))
    _print_trojan_check(analysis.get("trojan_ground_truth"))

    correlation = analysis.get("correlation")
    if correlation:
        print(f"\n  {'-' * 62}")
        print("  Correlated insights")
        print(f"  {'-' * 62}")
        print(f"    {correlation['summary']}")
        for item in correlation["correlated"]:
            print(f"    * {item['statement']}")

    print("\n" + "=" * 66 + "\n")


def summary_line(analysis: dict) -> str:
    """One line per netlist, for ``--quiet`` and batch runs."""
    name = analysis["circuit"]["name"]
    if analysis.get("sequential") is not None:
        r = analysis["sequential"]["analysis"]["results"]
        return (f"{name}: sequential engine, coverage {_fmt(r['fault_coverage'], ' %')}, "
                f"{_fmt(r['sequence_count'])} sequences")
    parts = [name + ":"]
    runs = analysis.get("runs") or {}
    standard = runs.get("non_targeted")
    targeted = runs.get("targeted")
    if standard and targeted:
        parts.append(f"standard {standard['test_vectors']['count']} -> targeted "
                     f"{targeted['test_vectors']['count']} patterns")
        cut = (analysis.get("comparison") or {}).get("pattern_reduction_pct")
        if cut is not None:
            parts.append(f"({cut} % fewer)")
    else:
        for run in runs.values():
            parts.append(f"{run['label']}: {run['test_vectors']['count']} patterns, "
                         f"coverage {_fmt(run['results']['fault_coverage'], ' %')}")
    verdict = analysis.get("verdict")
    if verdict:
        parts.append("| verdict: " + VERDICT_LABELS.get(verdict["verdict"], verdict["verdict"]))
    truth = analysis.get("trojan_ground_truth") or {}
    selection = truth.get("targeted_selection")
    if selection:
        parts.append(f"| Trojan gates in targeted list: {len(selection['trojan_gates_hit'])}"
                     f"/{len(truth['trojan_gates'])}")
    return " ".join(parts)
