"""RTL front end: synthesize behavioural Verilog into a gate-level netlist.

Most Trust-Hub chip-level Trojan benchmarks (AES-T*, BasicRSA-T*, RS232-T*,
PIC16F84-T*, wb_conmax-T*, ...) are distributed as RTL -- ``always`` blocks,
``case`` tables, arithmetic ``assign`` expressions -- spread over several
files, often zipped together with a Trojan-free copy and a testbench. The
structural parser (:mod:`pyatpg.core.verilog_parser`) only reads gate-level
netlists, so RTL is first run through Yosys:

* a ``.zip`` upload (or a directory on the CLI) is unpacked, testbenches are
  dropped, and when the archive holds both a Trojan-free and a
  Trojan-inserted copy of the design the Trojan-inserted one is chosen;
* Yosys flattens the design from its top module and maps it onto a tiny cell
  library (:data:`CELL_LIBERTY`) whose cell names the structural parser
  already lowers exactly (``AND2X1``, ``MUX21X1``, ``DFFX1``...);
* asynchronous set/reset is made synchronous and latches become a register
  plus a mux, since the ATPG model is a single-clock synchronous one;
* combinational feedback loops that survive synthesis (RTL that describes a
  register without a clock, e.g. AES-T900's ``always @(rst, out) Counter <=
  Counter + 1``) are cut with a pseudo flip-flop, see
  :func:`break_combinational_loops`.

Yosys is taken from ``PATH`` when installed, otherwise from the
``yowasp-yosys`` pip package (a WebAssembly build that needs no system
install). Results are cached on disk by source hash, so the several API
routes that parse the same upload synthesize it only once.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

#: Files Yosys reads as design sources.
_SOURCE_SUFFIXES = (".v", ".sv", ".verilog", ".vlog")
#: Headers that are only ever pulled in through ```include``.
_HEADER_SUFFIXES = (".vh", ".svh", ".inc")
_VHDL_SUFFIXES = (".vhd", ".vhdl")

_BUNDLE_MAGIC = "// pyatpg-rtl-bundle v1"
_FILE_MARKER = "// ==== pyatpg-file: {} ===="
_FILE_MARKER_RE = re.compile(r"^// ==== pyatpg-file: (.+) ====$", re.M)

#: Path components that mark the Trojan-inserted copy of a design in a
#: Trust-Hub archive (``src/TjIn``), versus the golden one (``src/TjFree``).
_TROJAN_DIR_RE = re.compile(r"^(tj_?in|trojan[_-]?in(serted)?|infected|with_?trojan)$", re.I)
_GOLDEN_DIR_RE = re.compile(r"^(tj_?free|trojan[_-]?free|golden|clean)$", re.I)

#: Cells the synthesized netlist is mapped onto. The names and pins are ones
#: :func:`pyatpg.core.verilog_parser._classify_cell` already lowers to its
#: exact Boolean function (output pin Y/Q, MUX select S).
CELL_LIBERTY = """\
library(pyatpg_generic) {
  cell(INVX1)   { area: 1; pin(A) { direction: input; } pin(Y) { direction: output; function: "!A"; } }
  cell(NBUFFX1) { area: 1; pin(A) { direction: input; } pin(Y) { direction: output; function: "A"; } }
  cell(AND2X1)  { area: 2; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "(A&B)"; } }
  cell(OR2X1)   { area: 2; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "(A|B)"; } }
  cell(NAND2X1) { area: 2; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "!(A&B)"; } }
  cell(NOR2X1)  { area: 2; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "!(A|B)"; } }
  cell(XOR2X1)  { area: 3; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "(A^B)"; } }
  cell(XNOR2X1) { area: 3; pin(A) { direction: input; } pin(B) { direction: input; } pin(Y) { direction: output; function: "!(A^B)"; } }
  cell(MUX21X1) { area: 4; pin(A) { direction: input; } pin(B) { direction: input; } pin(S) { direction: input; }
                  pin(Y) { direction: output; function: "((A&!S)|(B&S))"; } }
  cell(DFFX1)   { area: 6; ff(IQ, IQN) { clocked_on: "CLK"; next_state: "D"; }
                  pin(CLK) { direction: input; clock: true; } pin(D) { direction: input; }
                  pin(Q) { direction: output; function: "IQ"; } }
}
"""

# ``hierarchy``/``proc``/``flatten`` run explicitly first so ``tribuf`` can
# turn tri-states into logic before ``synth`` maps them; ``async2sync``
# folds asynchronous set/reset into the D logic and turns latches into a
# global-clock register plus a mux (``$_FF_``), which ``chtype`` then names
# as an ordinary flip-flop.
_SCRIPT = """\
read_verilog -sv {includes} {files}
hierarchy -check {top}
proc
tribuf -logic
flatten
synth -run coarse:
async2sync
dfflegalize -cell $_DFF_P_ 01
dfflibmap -liberty cells.lib
abc -liberty cells.lib
opt_clean
chtype -map $_FF_ DFFX1
stat
write_verilog -noattr -noexpr netlist.v
"""

_SYNTH_VERSION = "1"  # bump when the script or library changes, to invalidate the cache


class RtlSynthesisError(ValueError):
    """The RTL could not be synthesized; the message says why."""


class DesignTooLargeError(ValueError):
    """The design exceeds this server's size limits (see :func:`max_gates`)."""


#: Where the WebAssembly Yosys keeps its compiled machine code. Inside the
#: project so the copy build.sh warms up at deploy time is found at runtime
#: (a hosted service's home directory need not survive from build to run).
YOWASP_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".yowasp-cache"
)


def _limit(name: str, hosted_default: int) -> int:
    """An integer limit from the environment; 0 means unlimited.

    Without an explicit setting the limit applies only on the hosted
    backend (Render sets ``RENDER``), whose 512 MB instance a large design
    such as Trust-Hub AES (~190k gates) runs out of memory on; local runs
    stay unlimited.
    """
    value = os.environ.get(name)
    if value is not None and value.strip():
        try:
            return max(0, int(value))
        except ValueError:
            pass
    return hosted_default if os.environ.get("RENDER") else 0


def max_gates() -> int:
    """Largest design (gates + flip-flops) the server will analyse; 0 = no limit."""
    return _limit("PYATPG_MAX_GATES", 60000)


def yosys_memory_limit_mb() -> int:
    """Memory Yosys may use before synthesis is stopped; 0 = no limit."""
    return _limit("PYATPG_YOSYS_MAX_MB", 300)


def check_design_size(cells: int, what: str = "This design") -> None:
    """Raise :class:`DesignTooLargeError` when ``cells`` exceeds :func:`max_gates`."""
    limit = max_gates()
    if limit and cells > limit:
        raise DesignTooLargeError(
            f"{what} has {cells:,} gates and flip-flops, more than this server can analyse "
            f"({limit:,}); it would run out of memory. Try a smaller design, or run PyATPG "
            "locally / on a larger instance (the limit is PYATPG_MAX_GATES)."
        )


_CELL_LINE_RE = re.compile(r"^\s+[A-Z][A-Z0-9]*X1\s", re.M)


def count_synthesized_cells(netlist: str) -> int:
    """Number of library cells in a netlist :func:`synthesize` produced."""
    return len(_CELL_LINE_RE.findall(netlist))


# ----------------------------------------------------------------------
# Upload decoding and source bundles
# ----------------------------------------------------------------------

def decode_upload(filename: str, data: bytes) -> tuple[str, str]:
    """Turn an uploaded file into ``(source_text, filename)`` for the parsers.

    A zip archive becomes an RTL bundle (see :func:`pack_sources`) named
    ``<archive stem>.v``; anything else must be UTF-8 text and is returned
    unchanged.

    Raises:
        ValueError: undecodable text, or an archive with no usable sources.
    """
    filename = filename or "circuit.v"
    if filename.lower().endswith(".zip") or data[:4] == b"PK\x03\x04":
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{filename} is not a valid zip archive: {exc}") from exc
        files = {}
        for info in archive.infolist():
            if info.is_dir() or "__MACOSX" in info.filename:
                continue
            files[info.filename] = archive.read(info)
        return bundle_from_files(files, label=filename), _stem(filename) + ".v"
    try:
        return data.decode("utf-8"), filename
    except UnicodeDecodeError:
        try:
            # Old Trust-Hub sources carry the odd Latin-1 byte in comments.
            return data.decode("latin-1"), filename
        except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes anything
            raise ValueError("Circuit file must be text (or a .zip of Verilog sources).")


def read_path(path: str) -> tuple[str, str]:
    """CLI counterpart of :func:`decode_upload`: a file, zip or directory."""
    if os.path.isdir(path):
        files = {}
        for root, _, names in os.walk(path):
            for name in names:
                full = os.path.join(root, name)
                with open(full, "rb") as handle:
                    files[os.path.relpath(full, path).replace(os.sep, "/")] = handle.read()
        label = os.path.basename(os.path.normpath(path))
        return bundle_from_files(files, label=label), _stem(label) + ".v"
    with open(path, "rb") as handle:
        return decode_upload(os.path.basename(path), handle.read())


def bundle_from_files(files: dict[str, bytes], label: str = "archive") -> str:
    """Select the design sources out of ``files`` and pack them as a bundle."""
    texts = {}
    for name, data in files.items():
        if name.lower().endswith(_SOURCE_SUFFIXES + _HEADER_SUFFIXES + _VHDL_SUFFIXES):
            texts[name.replace("\\", "/")] = data.decode("utf-8", errors="replace")
    selected, notes = select_design_files(texts, label=label)
    return pack_sources(selected, notes)


def pack_sources(files: dict[str, str], notes: list[str] | None = None) -> str:
    """Join several source files into one text the parsers can carry around.

    The bundle is still valid Verilog (the markers are comments), so it can
    be stored, viewed and re-uploaded as a single ``.v`` file.
    """
    lines = [_BUNDLE_MAGIC]
    for note in notes or []:
        lines.append("// note: " + " ".join(note.split()))
    out = ["\n".join(lines)]
    for name in sorted(files):
        out.append(_FILE_MARKER.format(name))
        out.append(files[name].rstrip("\n"))
    return "\n".join(out) + "\n"


def is_bundle(text: str) -> bool:
    return text.startswith(_BUNDLE_MAGIC)


def unpack_sources(text: str) -> tuple[dict[str, str], list[str]]:
    """Inverse of :func:`pack_sources`: ``(files, notes)``."""
    first = _FILE_MARKER_RE.search(text)
    header = text[:first.start()] if first else text
    notes = [l[len("// note: "):] for l in header.splitlines() if l.startswith("// note: ")]
    files = {}
    if first:
        parts = _FILE_MARKER_RE.split(text[first.start():])
        # split() yields ["", name1, body1, name2, body2, ...]
        for name, body in zip(parts[1::2], parts[2::2]):
            files[name] = body.lstrip("\n")
    return files, notes


# ----------------------------------------------------------------------
# Design-file selection
# ----------------------------------------------------------------------

_MODULE_RE = re.compile(r"\b(?:module|macromodule)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(#\s*\(.*?\)\s*)?(\((.*?)\))?\s*;",
                        re.S)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _modules(text: str) -> list[tuple[str, bool]]:
    """``(name, has_ports)`` for every module declared in ``text``."""
    found = []
    for m in _MODULE_RE.finditer(_strip_comments(text)):
        ports = (m.group(4) or "").strip()
        found.append((m.group(1), bool(ports)))
    return found


_ENTITY_RE = re.compile(r"\bentity\s+(\w+)\s+is\b(.*?)\bend\b", re.I | re.S)


def _entities(text: str) -> list[tuple[str, bool]]:
    """``(name, has_ports)`` for every VHDL entity declared in ``text``."""
    text = re.sub(r"--[^\n]*", "", text)
    return [(m.group(1), bool(re.search(r"\bport\s*\(", m.group(2), re.I)))
            for m in _ENTITY_RE.finditer(text)]


def _is_vhdl_testbench(name: str, text: str) -> bool:
    ents = _entities(text)
    return bool(ents) and all(not has_ports for _, has_ports in ents)


def _is_testbench(name: str, text: str) -> bool:
    mods = _modules(text)
    if mods and all(not has_ports for _, has_ports in mods):
        return True  # a portless top-level module is a simulation harness
    base = posixpath.basename(name).lower()
    return bool(re.match(r"^(tb|test)[_\w]*\.s?v$|^\w*_tb\.s?v$|^glbl\.v$", base)) and "$finish" in text


def select_design_files(files: dict[str, str], label: str = "archive") -> tuple[dict[str, str], list[str]]:
    """Pick the synthesizable design out of an archive's text files.

    Returns the chosen files (sources and headers) and notes describing the
    choices made. Raises :class:`RtlSynthesisError` when nothing usable is left.
    """
    notes = []
    verilog = {n: t for n, t in files.items()
               if n.lower().endswith(_SOURCE_SUFFIXES + _HEADER_SUFFIXES + _VHDL_SUFFIXES)}
    if not verilog:
        raise RtlSynthesisError(f"{label} contains no Verilog (.v/.sv) or VHDL (.vhd) files.")

    benches = sorted(n for n, t in verilog.items()
                     if (n.lower().endswith(_SOURCE_SUFFIXES) and _is_testbench(n, t))
                     or (n.lower().endswith(_VHDL_SUFFIXES) and _is_vhdl_testbench(n, t)))
    for n in benches:
        del verilog[n]
    if benches:
        notes.append("Testbench file(s) left out of synthesis: "
                      + ", ".join(posixpath.basename(n) for n in benches))

    # Trust-Hub archives ship the golden and the Trojan-inserted design side
    # by side (src/TjFree, src/TjIn) with the same module names.
    if _duplicate_modules(verilog):
        def has_dir(name, pattern):
            return any(pattern.match(part) for part in name.split("/")[:-1])

        trojan = {n: t for n, t in verilog.items() if has_dir(n, _TROJAN_DIR_RE)}
        if trojan and not _duplicate_modules(trojan):
            dirs = sorted({posixpath.dirname(n) for n in trojan})
            notes.append(
                "The archive holds more than one copy of the design; synthesized the "
                f"Trojan-inserted one ({', '.join(dirs)}). Zip the Trojan-free folder on its "
                "own to analyse the golden circuit."
            )
            verilog = trojan
        else:
            clash = _duplicate_modules(verilog)
            sample = ", ".join(f"{m} ({', '.join(sorted(posixpath.dirname(f) or '.' for f in fs))})"
                               for m, fs in list(clash.items())[:3])
            raise RtlSynthesisError(
                "The archive defines the same module in more than one file, so it is ambiguous "
                f"which design to synthesize: {sample}. Zip only one design variant's folder."
            )
    if not any(n.lower().endswith(_SOURCE_SUFFIXES + _VHDL_SUFFIXES) for n in verilog):
        raise RtlSynthesisError(f"{label} has no design sources besides testbenches.")
    return verilog, notes


def _duplicate_modules(files: dict[str, str]) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for name, text in files.items():
        if name.lower().endswith(_SOURCE_SUFFIXES):
            mods = [m for m, _ in _modules(text)]
        elif name.lower().endswith(_VHDL_SUFFIXES):
            mods = [e.lower() for e, _ in _entities(text)]
        else:
            continue
        for mod in mods:
            seen.setdefault(mod, []).append(name)
    return {m: fs for m, fs in seen.items() if len(set(fs)) > 1}


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------

_ASSIGN_RE = re.compile(r"\bassign\b([^;]*);", re.S)
_BEHAVIOURAL_RE = re.compile(r"\b(always(_ff|_comb|_latch)?|initial|function|task|generate|case[xz]?)\b")
_OPERATOR_RE = re.compile(r"[&|^+\-*/%?<>!~=]")


def needs_synthesis(text: str) -> bool:
    """Is ``text`` RTL rather than a gate-level netlist the parser can read?

    Behavioural constructs or operator expressions inside a *design* module
    mean yes. Cell models shipped alongside a netlist (the ISCAS ``dff``
    module with its ``always @(posedge CK)``) do not count.
    """
    if is_bundle(text):
        return True
    from pyatpg.core.verilog_parser import _classify_cell

    body_re = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)(.*?)\bendmodule\b", re.S)
    for m in body_re.finditer(_strip_comments(text)):
        if _classify_cell(m.group(1)) is not None:
            continue
        body = m.group(2)
        if _BEHAVIOURAL_RE.search(body):
            return True
        for a in _ASSIGN_RE.finditer(body):
            for part in _split_assigns(a.group(1)):
                rhs = part.split("=", 1)[1] if "=" in part else ""
                rhs = rhs.strip()
                if rhs.startswith("~"):
                    rhs = rhs[1:]
                if _OPERATOR_RE.search(rhs):
                    return True
    return False


def _split_assigns(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


# ----------------------------------------------------------------------
# Synthesis
# ----------------------------------------------------------------------

def synthesize(text: str, filename: str = "design.v", top: str | None = None) -> tuple[str, list[str]]:
    """Synthesize RTL (a single file's text or a bundle) to a gate-level netlist.

    Returns ``(netlist_text, notes)``.

    Raises:
        RtlSynthesisError: Yosys is unavailable or rejected the design.
    """
    if is_bundle(text):
        files, notes = unpack_sources(text)
    else:
        files, notes = {posixpath.basename(filename.replace("\\", "/")) or "design.v": text}, []
    if not files:
        raise RtlSynthesisError("The RTL bundle contains no source files.")

    key = hashlib.sha256(
        (_SYNTH_VERSION + "\0" + (top or "") + "\0" + json.dumps(sorted(files.items()))).encode()
    ).hexdigest()
    cache_dir = os.path.join(tempfile.gettempdir(), "pyatpg_rtl_cache")
    cached = os.path.join(cache_dir, key + ".v")
    cached_notes = os.path.join(cache_dir, key + ".json")
    if os.path.exists(cached) and os.path.exists(cached_notes):
        with open(cached, encoding="utf-8") as h, open(cached_notes, encoding="utf-8") as n:
            return h.read(), notes + json.load(n)

    with tempfile.TemporaryDirectory(prefix="pyatpg_rtl_") as work:
        sources, include_dirs, has_module, vhdl = [], {"."}, {}, []
        for name, body in files.items():
            rel = _safe_relpath(name)
            full = os.path.join(work, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as h:
                h.write(body)
            include_dirs.add(posixpath.dirname(rel) or ".")
            if rel.lower().endswith(_SOURCE_SUFFIXES):
                sources.append(rel)
                has_module[rel] = bool(_modules(body))
            elif rel.lower().endswith(_VHDL_SUFFIXES):
                vhdl.append(rel)
        if vhdl:
            converted = _vhdl_to_verilog(work, vhdl, top if not sources else None)
            sources.append(converted)
            has_module[converted] = True
            notes = notes + [f"{len(vhdl)} VHDL file(s) converted to Verilog with GHDL before synthesis."]
        # Files holding only `define/`timescale go first so later files see them.
        sources.sort(key=lambda r: (has_module[r], r))
        with open(os.path.join(work, "cells.lib"), "w", encoding="utf-8") as h:
            h.write(CELL_LIBERTY)
        script = _SCRIPT.format(
            includes=" ".join(f"-I{_q(d)}" for d in sorted(include_dirs)),
            files=" ".join(_q(s) for s in sources),
            top=f"-top {top}" if top else "-auto-top",
        )
        with open(os.path.join(work, "synth.ys"), "w", encoding="utf-8") as h:
            h.write(script)
        log = _run_yosys(work)
        netlist_path = os.path.join(work, "netlist.v")
        if not os.path.exists(netlist_path):
            raise RtlSynthesisError("Yosys produced no netlist.\n" + _log_tail(log))
        with open(netlist_path, encoding="utf-8") as h:
            netlist = h.read()

    synth_notes = _synthesis_notes(log, len(sources))
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cached, "w", encoding="utf-8") as h:
            h.write(netlist)
        with open(cached_notes, "w", encoding="utf-8") as h:
            json.dump(synth_notes, h)
    except OSError:
        pass  # caching is an optimisation only
    return netlist, notes + synth_notes


def _safe_relpath(name: str) -> str:
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(re.sub(r"[^A-Za-z0-9_.+-]", "_", p) for p in parts) or "design.v"


def _vhdl_to_verilog(work: str, vhdl: list[str], top: str | None) -> str:
    """Elaborate the VHDL sources with GHDL and write them out as one Verilog file.

    GHDL (https://github.com/ghdl/ghdl) is the only open-source VHDL
    synthesizer Yosys works with; it is not on PyPI, so it must be installed
    separately (MSYS2: ``pacman -S mingw-w64-ucrt-x86_64-ghdl``; Debian/Ubuntu:
    ``apt install ghdl``).
    """
    ghdl = shutil.which("ghdl")
    if not ghdl:
        raise RtlSynthesisError(
            "This design is written in VHDL, which needs GHDL to synthesize. Install GHDL "
            "(MSYS2: pacman -S mingw-w64-ucrt-x86_64-ghdl; Linux: apt install ghdl) and retry, "
            "or upload a Verilog / gate-level version of the design."
        )
    timeout = float(os.environ.get("PYATPG_YOSYS_TIMEOUT", "3600"))

    def run(args):
        try:
            proc = subprocess.run([ghdl, *args], cwd=work, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RtlSynthesisError(f"GHDL did not finish within {timeout:.0f} s.")
        if proc.returncode != 0:
            raise RtlSynthesisError("GHDL could not process the VHDL:\n" + _log_tail(proc.stderr))
        return proc.stdout

    flags = ["--std=08", "-fsynopsys", "--workdir=."]
    run(["-i", *flags, *vhdl])
    if not top:
        found = run(["--find-top", *flags]).strip().split()
        if not found:
            raise RtlSynthesisError("GHDL could not determine the VHDL top-level entity.")
        top = found[-1]
    verilog = run(["--synth", *flags, "--out=verilog", top])
    out = "ghdl_" + re.sub(r"\W", "_", top) + ".v"
    with open(os.path.join(work, out), "w", encoding="utf-8") as h:
        h.write(verilog)
    return out


def _q(path: str) -> str:
    return '"' + path + '"' if " " in path else path


def _run_yosys(work: str) -> str:
    timeout = float(os.environ.get("PYATPG_YOSYS_TIMEOUT", "3600"))
    native = shutil.which("yosys")
    if native:
        cmd = [native, "-q", "-l", "yosys.log", "-s", "synth.ys"]
    else:
        try:
            import yowasp_yosys  # noqa: F401
        except ImportError:
            raise RtlSynthesisError(
                "This file is RTL (behavioural Verilog) and must be synthesized to gates first, "
                "but Yosys is not available. Install it with `pip install yowasp-yosys` "
                "(or put a native yosys on PATH)."
            )
        cmd = [sys.executable, "-c",
               "import sys, yowasp_yosys; sys.exit(yowasp_yosys.run_yosys(sys.argv[1:]))",
               "-q", "-l", "yosys.log", "-s", "synth.ys"]
    env = dict(os.environ)
    # build.sh compiles the WebAssembly Yosys into this directory once, so a
    # server does not redo that (slow, memory-hungry) step after every restart.
    env.setdefault("YOWASP_CACHE_DIR", YOWASP_CACHE_DIR)
    limit_mb = yosys_memory_limit_mb()
    with open(os.path.join(work, "stderr.txt"), "w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=err)
        started = time.monotonic()
        while proc.poll() is None:
            if time.monotonic() - started > timeout:
                proc.kill()
                proc.wait()
                raise RtlSynthesisError(f"Yosys synthesis did not finish within {timeout:.0f} s.")
            rss = _rss_mb(proc.pid)
            if limit_mb and rss and rss > limit_mb:
                proc.kill()
                proc.wait()
                raise DesignTooLargeError(
                    "This design is too large for this server: synthesizing it needed more than "
                    f"{limit_mb} MB of memory, so it was stopped before it could crash the backend. "
                    "Try a smaller design, or run PyATPG locally / on a larger instance "
                    "(the limit is PYATPG_YOSYS_MAX_MB)."
                )
            time.sleep(0.2)
        err.seek(0)
        stderr = err.read()
    log_path = os.path.join(work, "yosys.log")
    log = ""
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as h:
            log = h.read()
    if proc.returncode != 0:
        errors = [l for l in (log + "\n" + stderr).splitlines() if l.startswith("ERROR")]
        detail = "\n".join(dict.fromkeys(errors)) or _log_tail(log + stderr)
        raise RtlSynthesisError("Yosys could not synthesize the RTL:\n" + detail)
    return log


def _rss_mb(pid: int) -> int | None:
    """Resident memory of ``pid`` in MB, or None where it cannot be read.

    Reads /proc directly (Linux, i.e. the deployed backend) rather than
    depending on psutil; elsewhere the memory guard is simply inactive.
    """
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as h:
            for line in h:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _log_tail(log: str, lines: int = 15) -> str:
    return "\n".join(log.strip().splitlines()[-lines:])


def _synthesis_notes(log: str, n_files: int) -> list[str]:
    notes = []
    top = re.findall(r"Automatically selected (\S+) as design top module", log)
    if not top:
        top = re.findall(r"Top module:\s+\\?(\S+)", log)
    notes.append(
        f"RTL synthesized to gates with Yosys ({n_files} source file(s)"
        + (f", top module {top[-1].lstrip(chr(92))}" if top else "")
        + "). Asynchronous set/reset was made synchronous and all registers share one "
        "clock, matching the single-clock ATPG model."
    )
    latches = len(set(re.findall(r"Latch inferred for signal `([^']+)'", log)))
    if latches:
        notes.append(f"{latches} latch(es) inferred from the RTL are modelled as a register plus a mux.")
    return notes


# ----------------------------------------------------------------------
# Post-synthesis clean-up
# ----------------------------------------------------------------------

def remove_dead_logic(circuit) -> int:
    """Drop gates that reach no primary output or flip-flop; return how many.

    After ABC re-synthesizes the logic, Yosys keeps RTL wire names that no
    longer sit on any cell boundary as plain aliases with no driver (AES's
    ``p00[7:0]``, whose XOR ABC folded straight into the round function).
    They feed nothing, but left in place each would be promoted to a bogus
    primary input by :meth:`Circuit.promote_undefined_signals_to_inputs`.
    """
    ff_by_q = {ff.q: ff for ff in circuit.flipflops}
    live, stack = set(), list(circuit.outputs) + [ff.d for ff in circuit.flipflops]
    while stack:
        net = stack.pop()
        if net in live:
            continue
        live.add(net)
        gate = circuit.gates.get(net)
        if gate is not None:
            stack.extend(gate.inputs)
        ff = ff_by_q.get(net)
        if ff is not None:
            stack.append(ff.d)
    dead = [out for out in circuit.gates if out not in live]
    for out in dead:
        del circuit.gates[out]
    if dead:
        used = set(circuit.inputs) | set(circuit.outputs) | set(circuit.gates)
        for gate in circuit.gates.values():
            used.update(gate.inputs)
        for ff in circuit.flipflops:
            used.update((ff.q, ff.d))
        circuit.nodes = {n for n in circuit.nodes if n in used}
    return len(dead)


# ----------------------------------------------------------------------
# Combinational loops
# ----------------------------------------------------------------------

def break_combinational_loops(circuit) -> list[str]:
    """Cut every combinational cycle in ``circuit`` with a pseudo flip-flop.

    RTL such as ``always @(rst, out) Counter <= Counter + 1`` (AES-T900's
    trigger) describes state without a clock; synthesized literally it is a
    combinational loop no levelizing engine can process. The loop is cut at
    one of its nets -- preferring nets named in the RTL over synthesis
    temporaries, so ``Counter`` itself becomes the register -- by inserting a
    flip-flop there, which models the RTL's intent: the value updates once
    per evaluation, not continuously.

    Returns the cut net names (empty when the circuit was already acyclic).
    """
    import networkx as nx
    from pyatpg.core.netlist import FlipFlop

    gates = circuit.gates
    graph = nx.DiGraph()
    for out, gate in gates.items():
        for i in gate.inputs:
            if i in gates:
                graph.add_edge(i, out)

    cuts = []
    pending = [c for c in nx.strongly_connected_components(graph)
               if len(c) > 1 or any(graph.has_edge(n, n) for n in c)]
    while pending:
        comp = pending.pop()
        sub = graph.subgraph(comp)

        def score(n):
            named = not re.match(r"^_\d+_|.*\.n\d+$", n.rsplit("/", 1)[-1])
            return (named, sub.in_degree(n) * sub.out_degree(n), n)

        cut = max(comp, key=score)
        cuts.append(cut)
        rest = set(comp) - {cut}
        sub_rest = graph.subgraph(rest)
        pending.extend(c for c in nx.strongly_connected_components(sub_rest)
                       if len(c) > 1 or any(sub_rest.has_edge(n, n) for n in c))

    for net in cuts:
        gate = gates.pop(net)
        d = net + ".loop_d"
        gate.output = d
        gates[d] = gate
        circuit._add_node(d)
        circuit.add_flipflop(FlipFlop(net, d))
    return cuts


def _stem(name: str) -> str:
    base = posixpath.basename(name.replace("\\", "/"))
    for suffix in (".zip", ".verilog", ".sv", ".v"):
        if base.lower().endswith(suffix):
            return base[: -len(suffix)] or "design"
    return base or "design"
