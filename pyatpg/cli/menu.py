"""The interactive menu that plain ``pyatpg`` opens on a terminal.

Every item calls the same code as the matching subcommand, so the menu is a
guided way into the tool, not a second implementation. Results are saved to
an output folder (default ``pyatpg_out/``): the JSON report, the targeted test
patterns and the fault list for each analysed netlist.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from . import commands, ui

BANNER = r"""
  ____        _  _____ ____   ____
 |  _ \ _   _/ \|_   _|  _ \ / ___|
 | |_) | | | / _ \ | | | |_) | |  _
 |  __/| |_| / ___ \| | |  __/| |_| |
 |_|    \__, /_/   \_\_| |_|    \____|
        |___/
"""

ITEMS = [
    ("1", "Analyse a netlist", "standard vs targeted ATPG, verdict, Trojan check"),
    ("2", "Rank rare logic cones", "the targeted fault list in seconds, no ATPG"),
    ("3", "Insert a test Trojan", "into a clean netlist, with a trigger of chosen rarity"),
    ("4", "Bundled examples", "four Trojan circuits with known answers"),
    ("5", "Check setup", "ATALANTA, Yosys, LLM key"),
    ("6", "Settings", "full scan, language model, patterns, output folder"),
    ("7", "Command reference", "the same things as one-line commands"),
    ("8", "Web interface", "optional: start the web backend"),
]


class Session:
    def __init__(self) -> None:
        self.full_scan = True
        self.use_llm = False
        self.patterns = 3000
        self.out_dir = Path("pyatpg_out")
        self.top = 25
        self.llm_label = self._llm_label()

    @staticmethod
    def _llm_label() -> str | None:
        try:
            from pyatpg.ranking.providers import resolve_llm_config

            config = resolve_llm_config()
            return config.describe() if config else None
        except Exception:  # noqa: BLE001
            return None

    def status(self) -> str:
        llm = ("on: " + self.llm_label) if self.use_llm and self.llm_label else (
            "off" + (f" (key found: {self.llm_label})" if self.llm_label else " (no key)"))
        return (f"full scan {'on' if self.full_scan else 'off'} | language model {llm} | "
                f"output folder {self.out_dir}")


def _ask(prompt: str, default: str | None = None) -> str:
    hint = f" [{default}]" if default not in (None, "") else ""
    answer = input(f"  {prompt}{hint}: ").strip()
    return answer or (default or "")


def _ask_path(prompt: str) -> str | None:
    """A file or folder path; quotes from 'Copy as path' are stripped."""
    while True:
        raw = _ask(prompt + " (Enter to go back)")
        if not raw:
            return None
        path = raw.strip().strip('"').strip("'")
        if Path(path).exists():
            return path
        print(ui.warn(f"Not found: {path}"))


def _yes(prompt: str, default: bool) -> bool:
    answer = _ask(prompt + " (y/n)", "y" if default else "n").lower()
    return answer.startswith("y")


def _pause() -> None:
    try:
        input(ui.style("\n  Press Enter to return to the menu ", "dim"))
    except EOFError:
        pass


def _analyse(s: Session) -> None:
    path = _ask_path("Netlist (.bench, .v, .sv, .zip or RTL folder)")
    if not path:
        return
    mode = _ask("Mode: 1 compare both, 2 targeted only, 3 standard only", "1")
    mode = {"2": "targeted", "3": "non_targeted"}.get(mode, "compare")
    s.out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(path).name.split(".")[0]
    args = SimpleNamespace(
        netlist=path, mode=mode, full_scan=s.full_scan, no_llm=not s.use_llm,
        patterns=s.patterns, api_key=None, llm_provider=None, llm_model=None, llm_base_url=None,
        json=False, quiet=False, no_progress=False,
        output=str(s.out_dir / f"{stem}_report.json"),
        vectors_out=str(s.out_dir / f"{stem}_patterns.txt") if mode != "non_targeted" else None,
        faults_out=str(s.out_dir / f"{stem}_fault_list.txt") if mode != "non_targeted" else None,
    )
    commands.cmd_run(args)


def _rank(s: Session) -> None:
    path = _ask_path("Netlist to rank")
    if not path:
        return
    s.out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(path).name.split(".")[0]
    commands.cmd_rank(SimpleNamespace(
        netlist=path, patterns=s.patterns, top=s.top, json=False, no_progress=False,
        faults_out=str(s.out_dir / f"{stem}_fault_list.txt"),
        cones_out=str(s.out_dir / f"{stem}_cones.csv")))


def _trojan(s: Session) -> None:
    print(ui.style("  Inserts a 16-tap AND-tree trigger with XOR payloads (gates named Tj_*), "
                   "checked with ATALANTA to fire.", "dim"))
    host = _ask_path("Clean gate-level Verilog host (ISCAS primitives or SAED cells)")
    if not host:
        return
    cls = _ask("Trigger rarity: never / rare (0.1-1%) / uncommon (1-4%)", "uncommon")
    payloads = _ask("Payloads (host nets flipped)", "1")
    seed = _ask("Seed", "1")
    s.out_dir.mkdir(parents=True, exist_ok=True)
    out = s.out_dir / f"{Path(host).stem}_gen_{cls}_s{seed}.v"
    code = commands.cmd_trojan([host, "--class", cls, "--payloads", payloads, "--seed", seed,
                                "-o", str(out)])
    if code == 0 and _yes(f"Rank the new netlist now", True):
        commands.cmd_rank(SimpleNamespace(netlist=str(out), patterns=s.patterns, top=15,
                                          json=False, no_progress=False, faults_out=None,
                                          cones_out=None))


def _examples(s: Session) -> None:
    commands.print_examples()
    which = _ask("Example number (Enter to go back)")
    if not which:
        return
    action = _ask("1 rank only (seconds)  2 full analysis (ATALANTA)", "1")
    commands.cmd_examples(SimpleNamespace(
        which=which, rank=action != "2", llm=s.use_llm, top=15,
        output=str(s.out_dir / f"example_{which}_report.json") if action == "2" else None))


def _settings(s: Session) -> None:
    while True:
        ui.heading("Settings (this session only)")
        print(f"  1  Full scan for circuits with flip-flops : {'on' if s.full_scan else 'off'}"
              "   (off = sequential engine)")
        llm = "on" if s.use_llm else "off"
        print(f"  2  Language model                         : {llm}"
              f"   ({s.llm_label or 'no key found; see docs/configuration.md'})")
        print(f"  3  Toggle-profiling patterns              : {s.patterns}")
        print(f"  4  Output folder                          : {s.out_dir}")
        print(f"  5  Cones shown by 'rank'                  : {s.top}")
        choice = _ask("Change which (Enter to go back)")
        if not choice:
            return
        if choice == "1":
            s.full_scan = not s.full_scan
        elif choice == "2":
            if not s.llm_label and not s.use_llm:
                print(ui.warn("No key found. Put one in .env (copy .env.example) or set "
                              "GROQ_API_KEY, then restart."))
            else:
                s.use_llm = not s.use_llm
        elif choice == "3":
            value = _ask("Patterns", str(s.patterns))
            s.patterns = int(value) if value.isdigit() and int(value) > 0 else s.patterns
        elif choice == "4":
            s.out_dir = Path(_ask("Output folder", str(s.out_dir)).strip('"'))
        elif choice == "5":
            value = _ask("Cones (0 = all)", str(s.top))
            s.top = int(value) if value.isdigit() else s.top


def _reference(_: Session) -> None:
    ui.heading("The same things as one-line commands")
    rows = [
        ("Analyse (combinational)", "pyatpg run design.v --no-llm"),
        ("Analyse (with flip-flops)", "pyatpg run design.v --full-scan --no-llm"),
        ("Save patterns + fault list", "pyatpg run design.v --vectors-out p.txt --faults-out f.txt"),
        ("One line per file", "pyatpg run design.v --quiet"),
        ("JSON for scripts", "pyatpg run design.v --json | jq .comparison"),
        ("Rank cones only", "pyatpg rank design.v --top 30 --cones-out cones.csv"),
        ("Insert a test Trojan", "pyatpg trojan host.v --class uncommon --seed 3"),
        ("Examples", "pyatpg examples   /   pyatpg examples 2 --rank"),
        ("Check setup", "pyatpg doctor"),
        ("Web backend (optional)", "pyatpg serve"),
    ]
    ui.table(["Task", "Command"], rows)
    print("\n  Batch over a folder (bash):")
    print("    for f in netlists/*.v; do pyatpg run \"$f\" --full-scan --no-llm --quiet; done")
    print("  Full guide: docs/cli.md")


def _web(_: Session) -> None:
    if not _yes("Start the web backend on http://127.0.0.1:8000 (Ctrl+C stops it)", True):
        return
    try:
        commands.cmd_serve(SimpleNamespace(host="127.0.0.1", port=8000, reload=False))
    except KeyboardInterrupt:
        pass


ACTIONS = {"1": _analyse, "2": _rank, "3": _trojan, "4": _examples, "5": None,
           "6": _settings, "7": _reference, "8": _web}


def menu() -> int:
    s = Session()
    print(ui.style(BANNER, "cyan"))
    print(ui.style(f"  PyATPG {commands.version()}", "bold")
          + "  structure-guided ATPG for moderately rare Trojan triggers")
    while True:
        print()
        print(ui.style("  " + s.status(), "dim"))
        print()
        for key, title, hint in ITEMS:
            print(f"   {ui.style(key, 'bold', 'cyan')}  {title.ljust(24)} {ui.style(hint, 'dim')}")
        print(f"   {ui.style('q', 'bold', 'cyan')}  Quit")
        try:
            choice = input("\n  Choose: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("q", "quit", "exit"):
            return 0
        if choice == "5":
            try:
                commands.cmd_doctor(SimpleNamespace(quick=False))
            except KeyboardInterrupt:
                print("\n  Stopped.")
            _pause()
            continue
        action = ACTIONS.get(choice)
        if action is None:
            if choice:
                print(ui.warn("Pick a number from the list, or q to quit."))
            continue
        try:
            action(s)
        except KeyboardInterrupt:
            print("\n  Stopped.")
        except EOFError:
            print()
            return 0
        except Exception as exc:  # noqa: BLE001 - stay in the menu
            print(ui.bad(f"{exc}"))
        _pause()


if __name__ == "__main__":
    sys.exit(menu())
