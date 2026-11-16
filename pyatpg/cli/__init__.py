"""The ``pyatpg`` command.

    pyatpg                         interactive menu
    pyatpg run NETLIST [options]   standard vs targeted ATPG, verdict, Trojan check
    pyatpg NETLIST [options]       same as ``run`` (the original form)
    pyatpg rank NETLIST            ranked rare cones and the fault list, no ATPG
    pyatpg trojan HOST.v           insert a test Trojan of chosen rarity
    pyatpg examples [N]            list or run the bundled example circuits
    pyatpg doctor                  check ATALANTA, Yosys, the LLM key
    pyatpg serve                   start the optional web backend

Circuits with flip-flops go to the native sequential engine unless
``--full-scan`` is given (see ``AtpgAnalysisService.run``).
"""
from __future__ import annotations

import argparse
import sys

COMMANDS = ("run", "rank", "trojan", "examples", "doctor", "serve", "menu")

_EPILOG = """Plain 'pyatpg' opens an interactive menu. 'pyatpg NETLIST ...' is short for
'pyatpg run NETLIST ...'. Run 'pyatpg COMMAND -h' for a command's options.
Guide: docs/cli.md"""


def _add_llm_options(p: argparse.ArgumentParser) -> None:
    from pyatpg.ranking.providers import AUTO_PROVIDER, PROVIDER_ORDER

    g = p.add_argument_group("language model (optional)")
    g.add_argument("--no-llm", action="store_true",
                   help="Never call a language model, even if a key is set in the environment or .env")
    g.add_argument("--api-key", help="LLM key for this run (else LLM_API_KEY, GROQ_API_KEY, ... "
                   "from the environment or .env)")
    g.add_argument("--llm-provider", choices=[AUTO_PROVIDER, *PROVIDER_ORDER], default=AUTO_PROVIDER,
                   help="Vendor of the key (default: recognised from the key)")
    g.add_argument("--llm-model", help="Model id (each provider has a default)")
    g.add_argument("--llm-base-url", help="Endpoint for OpenAI-compatible servers, e.g. Ollama")


def build_parser() -> argparse.ArgumentParser:
    from pyatpg.atpg import AnalysisMode

    from .commands import version

    parser = argparse.ArgumentParser(
        prog="pyatpg",
        description="PyATPG: structure-guided ATPG prioritisation for moderately rare hardware "
                    "Trojan triggers in gate-level netlists.",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"pyatpg {version()}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    run = sub.add_parser("run", help="standard vs targeted ATPG on one netlist",
                         description="Run standard and targeted ATPG on a netlist, compare them, "
                                     "and report the Trojan verdict.")
    run.add_argument("netlist", help=".bench, gate-level or RTL .v/.sv, or a .zip/folder of RTL")
    run.add_argument("--mode", choices=[m.value for m in AnalysisMode], default="compare",
                     help="compare (default): both runs; targeted: ranked list only; "
                          "non_targeted: standard ATPG only")
    run.add_argument("--full-scan", action="store_true",
                     help="Treat every flip-flop as a scan cell (scan testing). Without it, "
                          "circuits with flip-flops go to the sequential engine")
    run.add_argument("--patterns", type=int, default=3000,
                     help="Random patterns for toggle-rate profiling (default 3000)")
    out = run.add_argument_group("output")
    out.add_argument("-o", "--output", default=None,
                     help="JSON report file (default atpg_report.json; with --json, none unless given)")
    out.add_argument("--json", action="store_true", help="Print the JSON report to stdout instead "
                     "of the text report (for jq and scripts)")
    out.add_argument("-q", "--quiet", action="store_true", help="Print one summary line only")
    out.add_argument("--vectors-out", metavar="FILE",
                     help="Write the targeted test patterns (standard ones in non_targeted mode)")
    out.add_argument("--faults-out", metavar="FILE",
                     help="Write the targeted fault list in ATALANTA's -f format")
    out.add_argument("--no-progress", action="store_true", help="No live progress line")
    _add_llm_options(run)

    rank = sub.add_parser("rank", help="ranked rare cones and the fault list, no ATPG",
                          description="Rank the netlist's rare logic cones the way the targeted "
                                      "fault list is built (structure and rarity in turn), without "
                                      "running ATALANTA or a model. Circuits with flip-flops are "
                                      "ranked on their full-scan view.")
    rank.add_argument("netlist", help=".bench, gate-level or RTL .v/.sv, or a .zip/folder of RTL")
    rank.add_argument("--top", type=int, default=25, help="Cones to show (default 25, 0 = all)")
    rank.add_argument("--patterns", type=int, default=3000, help="Toggle-profiling patterns")
    rank.add_argument("--full-scan", action="store_true", help=argparse.SUPPRESS)  # always on
    rank.add_argument("--json", action="store_true", help="Print the ranking as JSON")
    rank.add_argument("--faults-out", metavar="FILE", help="Write the fault list (ATALANTA -f format)")
    rank.add_argument("--cones-out", metavar="FILE", help="Write every ranked cone as CSV")
    rank.add_argument("--no-progress", action="store_true", help="No live progress line")

    sub.add_parser("trojan", add_help=False, help="insert a test Trojan of chosen rarity "
                   "('pyatpg trojan -h' for options)")

    ex = sub.add_parser("examples", help="list or run the bundled example circuits")
    ex.add_argument("which", nargs="?", help="Number (1-4) or file name; omit to list them")
    ex.add_argument("--rank", action="store_true", help="Only rank the cones (no ATALANTA)")
    ex.add_argument("--llm", action="store_true", help="Use a language model if a key is set")
    ex.add_argument("--top", type=int, default=15, help="Cones to show with --rank")
    ex.add_argument("-o", "--output", default=None, help="JSON report file")

    doc = sub.add_parser("doctor", help="check the installation")
    doc.add_argument("--quick", action="store_true", help="Skip the ATALANTA test run")

    srv = sub.add_parser("serve", help="start the optional web backend")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--reload", action="store_true", help="Reload on code changes")

    sub.add_parser("menu", help="interactive menu")
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .menu import menu

            sys.exit(menu())
        build_parser().print_help()
        sys.exit(0)
    if argv[0] == "trojan":
        from .commands import cmd_trojan

        sys.exit(cmd_trojan(argv[1:]))
    if argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["run", *argv]  # 'pyatpg NETLIST ...', the original form

    args = build_parser().parse_args(argv)
    from . import commands

    if args.command == "menu":
        from .menu import menu

        sys.exit(menu())
    handler = {
        "run": commands.cmd_run, "rank": commands.cmd_rank, "examples": commands.cmd_examples,
        "doctor": commands.cmd_doctor, "serve": commands.cmd_serve,
    }.get(args.command)
    if handler is None:
        build_parser().print_help()
        sys.exit(0)
    sys.exit(handler(args))
