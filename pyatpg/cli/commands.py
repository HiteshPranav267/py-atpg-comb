"""The ``pyatpg`` subcommands: run, rank, trojan, doctor, examples, serve."""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import ui
from .report import print_report, summary_line

#: Repository root (an editable install or a source checkout).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The bundled example circuits (examples/README.md has the full story).
#: Expected values: Trojan gates in the list without a model, from that README (PyATPG 1.1).
EXAMPLES = [
    {"file": "c3540_gen_uncommon_s10.v", "host": "ISCAS'85 c3540", "gates": 1681, "flip_flops": 0,
     "trigger": "uncommon, 3.9%", "trojan_gates": 12, "rarity_only": 2, "score": 12,
     "patterns": "270 -> 57", "note": "trigger is 123rd by rarity, 1st by structure"},
    {"file": "s5378_gen_rare_s9.v", "host": "ISCAS'89 s5378", "gates": 2794, "flip_flops": 179,
     "trigger": "rare, 0.68%", "trojan_gates": 15, "rarity_only": 0, "score": 15,
     "patterns": "649 -> 83", "note": "trigger is 50th by rarity, 17th by structure; full scan"},
    {"file": "s9234_gen_uncommon_s10.v", "host": "ISCAS'89 s9234", "gates": 5609, "flip_flops": 211,
     "trigger": "uncommon, 2.4%", "trojan_gates": 12, "rarity_only": 2, "score": 2,
     "patterns": "957 -> 75", "note": "trigger is 32nd by structure, the list holds 29 cones; PYATPG_SCORE_MODE=single gives 12 of 12"},
    {"file": "b17_gen_rare_s9.v", "host": "ITC'99 b17", "gates": 22660, "flip_flops": 0,
     "trigger": "rare, 0.78%", "trojan_gates": 15, "rarity_only": 5, "score": 5,
     "patterns": "6,439 -> 163", "note": "the limit: >900 rare cones, about 2 minutes"},
]

#: ISCAS'85 c17, used by ``doctor`` to check that ATALANTA runs.
C17_BENCH = """# c17
INPUT(1)
INPUT(2)
INPUT(3)
INPUT(6)
INPUT(7)
OUTPUT(22)
OUTPUT(23)
10 = NAND(1, 3)
11 = NAND(3, 6)
16 = NAND(2, 11)
19 = NAND(11, 7)
22 = NAND(10, 16)
23 = NAND(16, 19)
"""


def version() -> str:
    try:
        from importlib.metadata import version as _v

        return _v("pyatpg")
    except Exception:  # noqa: BLE001 - running from a checkout without installing
        return "dev"


def _info(message: str, to_stderr: bool) -> None:
    print(message, file=sys.stderr if to_stderr else sys.stdout)


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------

def write_vectors(path: str, analysis: dict, which: str | None = None) -> str | None:
    """Write one run's test patterns as plain text; returns the run used, or None."""
    runs = analysis.get("runs") or {}
    key = which or ("targeted" if "targeted" in runs else "non_targeted")
    run = runs.get(key)
    if not run:
        return None
    tv = run["test_vectors"]
    lines = [
        f"# PyATPG test patterns: {analysis['circuit']['name']}, "
        f"{'targeted' if key == 'targeted' else 'standard (non-targeted)'} run, "
        f"{len(tv['vectors'])} patterns",
        "# inputs, in bit order: " + " ".join(tv.get("primary_inputs") or []),
        "# outputs, in bit order: " + " ".join(tv.get("primary_outputs") or []),
        "# each line: <input bits> <fault-free output bits>",
    ]
    lines += [f"{v['inputs']} {v['outputs']}" for v in tv["vectors"]]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return key


def write_fault_list(path: str, gates: list[str]) -> None:
    """The targeted fault list in ATALANTA's ``-f`` format (stuck-at-0 and -1 per gate)."""
    from pyatpg.atpg.runner import build_fault_lines

    Path(path).write_text("\n".join(build_fault_lines(gates)) + "\n", encoding="utf-8")


def run_analysis(netlist: str, *, mode: str = "compare", full_scan: bool = False,
                 use_llm: bool = True, patterns: int = 3000, api_key: str | None = None,
                 llm_provider: str | None = None, llm_model: str | None = None,
                 llm_base_url: str | None = None, show_progress: bool = True) -> dict:
    """Run one analysis and return it as the JSON-ready dict."""
    from pyatpg.atpg import AnalysisMode, AtpgAnalysisService
    from pyatpg.core.rtl import read_path

    source_text, name = read_path(netlist)
    service = AtpgAnalysisService(api_key=api_key, llm_provider=llm_provider,
                                  llm_model=llm_model, llm_base_url=llm_base_url)
    with ui.progress(show_progress):
        analysis = service.run(source_text=source_text, filename=name, mode=AnalysisMode(mode),
                               toggle_patterns=patterns, use_llm=use_llm, full_scan=full_scan)
    return analysis.to_dict()


def cmd_run(args) -> int:
    machine = args.json  # stdout carries only the JSON
    try:
        payload = run_analysis(
            args.netlist, mode=args.mode, full_scan=args.full_scan, use_llm=not args.no_llm,
            patterns=args.patterns, api_key=args.api_key, llm_provider=args.llm_provider,
            llm_model=args.llm_model, llm_base_url=args.llm_base_url,
            show_progress=not (args.quiet or args.no_progress),
        )
    except (OSError, ValueError) as exc:
        print(ui.bad(f"Could not analyse {args.netlist}: {exc}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - surface the real reason and stop
        print(ui.bad(f"Analysis failed: {exc}"), file=sys.stderr)
        return 1

    if machine:
        json.dump(payload, sys.stdout, indent=2, default=str)
        print()
    elif args.quiet:
        print(summary_line(payload))
    else:
        print_report(payload)

    output = args.output if args.output is not None else (None if machine else "atpg_report.json")
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        if not args.quiet:
            _info(f"Report written to {output}", machine)
    if args.vectors_out:
        used = write_vectors(args.vectors_out, payload)
        if used:
            _info(f"{'Targeted' if used == 'targeted' else 'Standard'} test patterns written "
                  f"to {args.vectors_out}", machine or args.quiet)
        else:
            _info(ui.warn("No ATALANTA patterns to write (sequential run?)"), True)
    if args.faults_out:
        gates = (payload.get("targeting") or {}).get("gates") or []
        if gates:
            write_fault_list(args.faults_out, gates)
            _info(f"Targeted fault list ({len(gates)} gates, {2 * len(gates)} faults) written "
                  f"to {args.faults_out}", machine or args.quiet)
        else:
            _info(ui.warn("No targeted fault list in this run (mode or sequential path)"), True)
    return 0


# ----------------------------------------------------------------------
# rank
# ----------------------------------------------------------------------

def print_rank(result, top: int | None) -> None:
    ui.heading(f"Rare cones of {result.name}")
    kind = f", {result.flip_flops} flip-flops (full-scan view)" if result.flip_flops else ""
    print(f"  {result.gates:,} gates, {result.inputs} inputs, {result.outputs} outputs{kind}")
    print(f"  {result.rare_cones} rare cones (a net true or false in under 5% of random "
          f"patterns, plus the logic that feeds only it)")
    in_list = sum(c.in_list for c in result.cones)
    print(f"  Targeted fault list: {len(result.fault_list)} gates, holding {in_list} of the "
          f"ranked cones")
    if not result.cones:
        print("\n  No rare cones to rank: the fault list is the structural heuristic's picks.")
        return
    shown = result.cones if not top else result.cones[:top]
    known = result.trojan_gates > 0
    headers = ["#", "Root net", "Gates", "Taps", "Fires", "Rarity #", "Score", "Rare in",
               "Picked by", "In list"] + (["Trojan"] if known else [])
    rows = []
    for c in shown:
        row = [c.place, c.root[:28], c.gates, c.taps, f"{c.fires_pct:.2f}%", c.rarity_place,
               f"{c.score:.2f}", f"{c.rare_inputs:.2f}", c.picked_by, "yes" if c.in_list else "-"]
        if known:
            row.append(c.trojan_gates or "")
        rows.append(row)
    print()
    ui.table(headers, rows, align="rlrrrrrrll" + ("r" if known else ""),
             highlight=[bool(c.trojan_gates) for c in shown] if known else None)
    if top and len(result.cones) > top:
        print(ui.style(f"  ... {len(result.cones) - top} more (--top 0 shows all)", "dim"))
    print(ui.style("  Fires: how often the root takes its rarer value. Rarity #: its place in plain "
                   "rarity order.\n  Score / Rare in: the two structural templates. The list takes "
                   "cones from the templates and rarity order in turn.", "dim"))
    if known:
        labels = {"R": "Rarity only", "R+Rand1": "Rarity + random (seed 1)",
                  "R+Rand2": "Rarity + random (seed 2)", "R+Rand3": "Rarity + random (seed 3)",
                  "R+S": "Rarity + structure (default)"}
        ui.heading(f"Trojan check: {result.trojan_gates} Trojan gates known by name")
        rows = []
        for key, row in result.orderings.items():
            first = row.get("first_trojan_cone")
            rows.append([labels.get(key, key), f"{row['trojan_hits']} / {result.trojan_gates}",
                         f"#{first} of {row['cones']}" if first else "none"])
        ui.table(["Ordering (same list size)", "Trojan gates in list", "First Trojan cone"], rows,
                 align="lrr", highlight=[k == "R+S" for k in result.orderings])


def cmd_rank(args) -> int:
    from .rank import rank_netlist

    try:
        with ui.progress(not (args.json or args.no_progress)):
            result = rank_netlist(args.netlist, toggle_patterns=args.patterns)
    except (OSError, ValueError) as exc:
        print(ui.bad(f"Could not rank {args.netlist}: {exc}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    if args.json:
        json.dump(result.to_dict(), sys.stdout, indent=2)
        print()
    else:
        print_rank(result, args.top)
        for note in result.notes:
            if note.startswith("Ranked on the full-scan model"):
                print(ui.style(f"  Note: {note}", "dim"))
        print()
    if args.faults_out:
        write_fault_list(args.faults_out, result.fault_list)
        _info(f"Fault list ({len(result.fault_list)} gates) written to {args.faults_out}", args.json)
    if args.cones_out:
        with open(args.cones_out, "w", newline="", encoding="utf-8") as handle:
            rows = [c.to_dict() for c in result.cones]
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["place"])
            writer.writeheader()
            writer.writerows(rows)
        _info(f"Ranked cones written to {args.cones_out}", args.json)
    return 0


# ----------------------------------------------------------------------
# trojan
# ----------------------------------------------------------------------

def cmd_trojan(argv: list[str]) -> int:
    from pyatpg.trojan.rare_trigger import main as trojan_main

    try:
        trojan_main(argv, prog="pyatpg trojan")
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    return 0


# ----------------------------------------------------------------------
# doctor
# ----------------------------------------------------------------------

def _windows_debug_build(path: Path) -> bool:
    return platform.system() == "Windows" and path.name.lower() == "atalanta.exe"


def check_setup(run_smoke: bool = True) -> tuple[list[tuple[str, str, str]], bool]:
    """(rows of (status, item, detail), ATALANTA usable). Status: ok / warn / bad / skip."""
    rows: list[tuple[str, str, str]] = []
    py = sys.version_info
    rows.append(("ok" if py >= (3, 11) else "bad", "Python",
                 f"{py.major}.{py.minor}.{py.micro}" + ("" if py >= (3, 11) else " (needs 3.11+)")))
    rows.append(("ok", "PyATPG", version()))

    atalanta_ok = False
    try:
        from pyatpg.atpg.binary import resolve_atalanta_binary

        binary = resolve_atalanta_binary()
        if _windows_debug_build(binary.path) and not os.environ.get("ATALANTA_BIN"):
            rows.append(("warn", "ATALANTA",
                         f"{binary.path} is a Windows build that can pop up 'Debug Error' dialogs; "
                         "build it under WSL and set ATALANTA_BIN (docs/configuration.md)"))
        elif run_smoke:
            from pyatpg.atpg import AnalysisMode, AtpgAnalysisService

            started = time.monotonic()
            result = AtpgAnalysisService().run(C17_BENCH, "c17.bench", mode=AnalysisMode.NON_TARGETED,
                                               use_llm=False)
            coverage = result.to_dict()["runs"]["non_targeted"]["results"]["fault_coverage"]
            atalanta_ok = coverage is not None and float(coverage) > 99
            rows.append(("ok" if atalanta_ok else "bad", "ATALANTA",
                         f"{binary.path}  (c17 test: {coverage}% coverage in "
                         f"{time.monotonic() - started:.1f} s)"))
        else:
            atalanta_ok = True
            rows.append(("ok", "ATALANTA", str(binary.path)))
    except Exception as exc:  # noqa: BLE001
        rows.append(("bad", "ATALANTA", f"{exc}".split(". Build")[0]
                     + ". Needed by 'run'; 'rank' works without it. See README, Install."))

    if shutil.which("yosys"):
        rows.append(("ok", "Yosys (RTL input)", f"native: {shutil.which('yosys')}"))
    elif importlib.util.find_spec("yowasp_yosys"):
        rows.append(("ok", "Yosys (RTL input)", "pip package yowasp-yosys"))
    else:
        rows.append(("warn", "Yosys (RTL input)", "not found: RTL files cannot be synthesised "
                     "(pip install yowasp-yosys); gate-level netlists are fine"))
    rows.append(("ok" if shutil.which("ghdl") else "skip", "GHDL (VHDL input)",
                 shutil.which("ghdl") or "not found (only needed for VHDL)"))

    try:
        from pyatpg.ranking.providers import resolve_llm_config

        config = resolve_llm_config()
        rows.append(("ok", "Language model", f"{config.describe()} (key from the environment or "
                     ".env; --no-llm turns it off)") if config else
                    ("skip", "Language model", "no key: the structural score ranks the cones "
                     "(the default, as good in our tests)"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("warn", "Language model", f"key found but not usable: {exc}"))

    web = all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn"))
    rows.append(("ok" if web else "skip", "Web interface (optional)",
                 "backend packages installed ('pyatpg serve')" if web else
                 'not installed (pip install -e ".[web]")'))
    return rows, atalanta_ok


def cmd_doctor(args) -> int:
    ui.heading("PyATPG setup check")
    rows, atalanta_ok = check_setup(run_smoke=not args.quick)
    mark = {"ok": ui.ok, "warn": ui.warn, "bad": ui.bad, "skip": ui.skip}
    width = max(len(item) for _, item, _ in rows)
    for status, item, detail in rows:
        print("  " + mark[status](f"{item.ljust(width)}  {detail}"))
    print()
    if atalanta_ok:
        print("  Ready. Try:  pyatpg examples 1")
    else:
        print("  'pyatpg rank' works now; 'pyatpg run' needs ATALANTA.")
    print()
    return 0 if atalanta_ok else 1


# ----------------------------------------------------------------------
# examples
# ----------------------------------------------------------------------

def examples_dir() -> Path | None:
    for base in (REPO_ROOT / "examples", Path.cwd() / "examples"):
        if (base / EXAMPLES[0]["file"]).is_file():
            return base
    return None


def resolve_example(which: str) -> dict | None:
    if which.isdigit() and 1 <= int(which) <= len(EXAMPLES):
        return EXAMPLES[int(which) - 1]
    for ex in EXAMPLES:
        if which in (ex["file"], ex["file"].rsplit(".", 1)[0]) or ex["file"].startswith(which):
            return ex
    return None


def print_examples() -> None:
    ui.heading("Bundled example circuits (one inserted Trojan each)")
    rows = [[i, ex["file"], ex["host"], f"{ex['gates']:,}", ex["flip_flops"], ex["trigger"],
             f"{ex['rarity_only']} / {ex['trojan_gates']}", f"{ex['score']} / {ex['trojan_gates']}",
             ex["patterns"]] for i, ex in enumerate(EXAMPLES, 1)]
    ui.table(["#", "File", "Host", "Gates", "FFs", "Trigger fires", "Rarity only",
              "Structure", "Patterns"], rows, align="rllrrlrrl")
    print(ui.style("  Rarity only / Structure: Trojan gates in the fault list (no model). "
                   "Patterns: standard -> targeted.", "dim"))
    print("\n  Run one:   pyatpg examples 1          (full analysis, needs ATALANTA)")
    print("  Rank one:  pyatpg examples 1 --rank   (seconds, no ATALANTA)")
    print("  Details:   examples/README.md\n")


def cmd_examples(args) -> int:
    if not args.which:
        print_examples()
        return 0
    ex = resolve_example(args.which)
    folder = examples_dir()
    if ex is None:
        print(ui.bad(f"No example {args.which!r}; 'pyatpg examples' lists them."), file=sys.stderr)
        return 2
    if folder is None:
        print(ui.bad("The examples/ folder was not found (it ships with the source checkout)."),
              file=sys.stderr)
        return 1
    path = str(folder / ex["file"])
    print(ui.style(f"  {ex['file']}: {ex['host']}, {ex['note']}. Expected without a model: "
                   f"rarity only {ex['rarity_only']}/{ex['trojan_gates']}, structure "
                   f"{ex['score']}/{ex['trojan_gates']} Trojan gates.", "dim"))
    if args.rank:
        from types import SimpleNamespace

        return cmd_rank(SimpleNamespace(netlist=path, patterns=3000, top=args.top, json=False,
                                        no_progress=False, faults_out=None, cones_out=None))
    from types import SimpleNamespace

    return cmd_run(SimpleNamespace(
        netlist=path, mode="compare", full_scan=ex["flip_flops"] > 0, no_llm=not args.llm,
        patterns=3000, api_key=None, llm_provider=None, llm_model=None, llm_base_url=None,
        json=False, quiet=False, no_progress=False, output=args.output, vectors_out=None,
        faults_out=None))


# ----------------------------------------------------------------------
# serve
# ----------------------------------------------------------------------

def cmd_serve(args) -> int:
    if not all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn")):
        print(ui.bad('The web interface needs its optional packages: pip install -e ".[web]"'),
              file=sys.stderr)
        return 1
    import uvicorn

    print(f"  Backend on http://{args.host}:{args.port}  (API docs at /docs)")
    web = REPO_ROOT / "web"
    if (web / "package.json").is_file():
        print(f"  Frontend:  cd {web} && npm install && npm run dev   (http://localhost:5173)")
    print("  Stop with Ctrl+C.\n")
    uvicorn.run("pyatpg.api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0
