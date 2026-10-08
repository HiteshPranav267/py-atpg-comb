"""Structural (gate-level) Verilog parser.

Reads the two netlist styles this project meets:

* ISCAS-style netlists built from Verilog primitives (``and``, ``nand``,
  ``not`` ...) and the ``dff`` / ``delta`` / ``sdff`` register cells, with
  positional connections; and
* synthesized netlists built from standard-cell library instances (SAED
  ``NAND2X0``/``AO22X1``/``SDFFX1``..., GSCLib ``AOI21X1``/``MX2X1``...) with
  named connections, buses, ``assign`` aliases, tie-off constants and module
  hierarchy (e.g. Trust-Hub Trojan benchmarks, where the Trojan is its own
  module instantiated inside the host circuit).

The design is flattened from its top module, and every library cell is lowered
to the primitive gates :class:`Circuit` supports (AND/OR/NAND/NOR/NOT/XOR/BUF)
with its exact Boolean function -- an ``AO22`` becomes two ANDs feeding an OR,
not an approximating single gate -- because ATPG on an approximated netlist
generates tests for a different circuit. Constants (``1'b0``, ``1'b1``,
``supply0``/``supply1`` nets, TIE cells) are folded away, since the circuit
model has no constant source. A cell type this parser cannot model exactly is
an error rather than a silent guess.
"""
import re
from pyatpg.core.netlist import Circuit, FlipFlop, ScanFlipFlop

CONST0 = "1'b0"
CONST1 = "1'b1"
#: Tie-off nets standing in for a constant flip-flop pin: "__const0"/"__const1".
TIE_PREFIX = "__const"
#: Flip-flop clock pin names across the supported cell libraries.
_CLOCK_PINS = ("CLK", "CK", "CP", "C", "CLOCK", "CLKN", "CKN", "GCLK")
_CONSTS = {CONST0: 0, CONST1: 1}

# Hierarchical instance separator for flattened net names. Net names must
# stay within what Atalanta's fault-list reader accepts -- letters, digits,
# "_" and "[]" (Atalanta/define_fault_list.cpp, is_valid) -- or a targeted run,
# which hands Atalanta a fault list, dies with "Error in fault file". "/" and
# "." both used to appear here and broke targeting on every hierarchical
# netlist (all Trust-Hub gate-level Trojan benchmarks).
_HIER_SEP = "__"

_DECL_KEYWORDS = ("input", "output", "inout")
_NET_KEYWORDS = ("wire", "tri", "reg", "trireg", "wand", "wor", "tri0", "tri1", "logic")
_SKIP_KEYWORDS = ("always", "initial", "parameter", "localparam", "defparam",
                  "specify", "timeunit", "timeprecision", "genvar", "integer",
                  "begin", "end", "function", "task", "`")

_PRIMITIVES = {"and": "AND", "nand": "NAND", "or": "OR", "nor": "NOR",
               "xor": "XOR", "xnor": "XNOR", "not": "NOT", "buf": "BUF"}

# Output pin names used by standard-cell libraries (SAED, GSCLib, Nangate...).
_OUTPUT_PINS = {"Q", "QN", "Y", "YN", "Z", "ZN", "O", "OUT"}
# Adders: SAED HADD (A0, B0 -> C1, SO) / FADD (A, B, CI -> CO, S), GSCLib ADDH/ADDF.
_ADDER_OUTPUT_PINS = {"SO", "S", "CO", "C1"}

_NUM_RE = re.compile(r"^(\d*)'([sS]?)([bBoOdDhH])([0-9a-fA-FxXzZ_?]+)$")
_IDENT_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_$]*)(?:\[(\s*-?\d+\s*)(?::(\s*-?\d+\s*))?\])?$")


def _natural_key(pin):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pin)]


def _split_top(text, sep=","):
    """Split on ``sep`` outside of (), {} and []."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _match_paren(text, start):
    """Index of the ``)`` closing the ``(`` at ``text[start]``."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"Unbalanced parentheses near: {text[start:start + 80]!r}")


class _Module:
    def __init__(self, name):
        self.name = name
        self.ports = []          # port names in declaration order
        self.directions = {}     # port name -> input/output/inout
        self.ranges = {}         # net name -> (msb, lsb)
        self.supplies = {}       # net name -> 0/1
        self.assigns = []        # (lhs_expr, rhs_expr)
        self.instances = []      # (cell_type, inst_name, conns)
                                 # conns: dict pin->expr (named) or list of exprs


class VerilogParser:
    def __init__(self):
        self.circuit = Circuit()
        #: Human-readable remarks about how the netlist was interpreted
        #: (constant outputs dropped, cells lowered with an assumed function).
        self.notes = []

    # ------------------------------------------------------------------
    # Lexing / module extraction
    # ------------------------------------------------------------------

    @staticmethod
    def clean(text):
        """Remove comments, attributes and compiler directives; unescape identifiers."""
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)
        text = re.sub(r"^\s*`[^\n]*", "", text, flags=re.M)
        # Escaped identifiers (``\foo[3] ``) end at whitespace; keep the name
        # with any special characters replaced so it stays a single token.
        # The whitespace that terminates one may precede a bit select
        # (Yosys writes ``\AES.r2.p33 [31:20]``); drop it so the select
        # stays attached to the name.
        text = re.sub(r"\\(\S+)(?:\s+(?=\[))?",
                      lambda m: re.sub(r"[^A-Za-z0-9_]", "_", m.group(1)), text)
        return text

    def _parse_modules(self, text):
        modules = {}
        for m in re.finditer(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)(.*?)\bendmodule\b", text, re.S):
            mod = _Module(m.group(1))
            body = m.group(2)
            header, sep, rest = body.partition(";")
            header = re.sub(r"^\s*#\s*\(.*?\)\s*", "", header, flags=re.S).strip()
            if header.startswith("("):
                inner = header[1:header.rindex(")")]
                self._parse_port_header(mod, inner)
            for stmt in rest.split(";"):
                self._parse_statement(mod, " ".join(stmt.split()))
            modules[mod.name] = mod
        return modules

    def _parse_port_header(self, mod, inner):
        direction, rng = None, None
        for item in _split_top(inner):
            if not item:
                continue
            tokens = item.split()
            if tokens[0] in _DECL_KEYWORDS:  # ANSI-style port declaration
                direction, rng, names = self._parse_decl(item)
                name = names[0]
            else:
                name = tokens[-1]
                if direction is not None:
                    self._declare(mod, direction, rng, [name])
            mod.ports.append(name)
            if tokens[0] in _DECL_KEYWORDS:
                self._declare(mod, direction, rng, [name])

    @staticmethod
    def _parse_decl(stmt):
        """``input wire [3:0] a, b`` -> ("input", (3, 0), ["a", "b"])."""
        m = re.match(r"^(\w+)\s*(?:(?:wire|reg|tri|logic|signed)\s*)*(\[[^\]]*\])?\s*(.*)$", stmt)
        kind, rng_text, names_text = m.group(1), m.group(2), m.group(3)
        rng = None
        if rng_text:
            msb, lsb = rng_text[1:-1].split(":")
            rng = (int(msb), int(lsb))
        names = [n.split("=")[0].strip() for n in names_text.split(",") if n.strip()]
        return kind, rng, names

    @staticmethod
    def _declare(mod, direction, rng, names):
        for n in names:
            if direction in _DECL_KEYWORDS:
                mod.directions[n] = direction
            if rng is not None:
                mod.ranges[n] = rng

    def _parse_statement(self, mod, stmt):
        if not stmt or stmt in ("endmodule",):
            return
        first = stmt.split()[0]
        if first.startswith(_SKIP_KEYWORDS) and first not in _PRIMITIVES:
            return
        if first in _DECL_KEYWORDS or first in _NET_KEYWORDS:
            kind, rng, names = self._parse_decl(stmt)
            self._declare(mod, kind, rng, names)
            if first in _NET_KEYWORDS and "=" in stmt:  # ``wire a = b;``
                for part in _split_top(stmt[len(first):].lstrip()):
                    if "=" in part:
                        lhs, rhs = part.split("=", 1)
                        lhs = re.sub(r"^\[[^\]]*\]\s*", "", lhs.strip())
                        mod.assigns.append((lhs.strip(), rhs.strip()))
            return
        if first in ("supply0", "supply1"):
            for n in stmt[len(first):].split(","):
                if n.strip():
                    mod.supplies[n.strip()] = 0 if first == "supply0" else 1
            return
        if first == "assign":
            for part in _split_top(stmt[len("assign"):]):
                lhs, rhs = part.split("=", 1)
                mod.assigns.append((lhs.strip(), rhs.strip()))
            return
        self._parse_instance(mod, stmt)

    def _parse_instance(self, mod, stmt):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)\s*(#\s*\(.*?\))?\s*", stmt)
        if not m or "(" not in stmt:
            if stmt.startswith("$") and "(" in stmt:
                # A synthesis tool's internal cell (Yosys ``\$_DFF_P_``):
                # dropping it would silently delete logic.
                raise ValueError(f"Unsupported cell type in the netlist: {stmt.split()[0]}")
            return
        cell = m.group(1)
        rest = stmt[m.end():]
        # One statement may declare several instances: ``nand g1(...), g2(...)``.
        for idx, piece in enumerate(_split_top(rest)):
            piece = piece.strip()
            pm = re.match(r"^([A-Za-z_][A-Za-z0-9_$]*)?\s*(\[[^\]]*\])?\s*\(", piece)
            if not pm:
                continue
            name = pm.group(1) or f"{cell}_{len(mod.instances)}"
            open_idx = pm.end() - 1
            close_idx = _match_paren(piece, open_idx)
            conns_text = piece[open_idx + 1:close_idx]
            items = _split_top(conns_text)
            if items and all(i.startswith(".") for i in items if i):
                named = {}
                for item in items:
                    if not item:
                        continue
                    nm = re.match(r"^\.\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\((.*)\)$", item, re.S)
                    if not nm:
                        raise ValueError(f"Malformed port connection {item!r} in {cell} {name}")
                    named[nm.group(1)] = nm.group(2).strip()
                mod.instances.append((cell, name, named))
            else:
                mod.instances.append((cell, name, [i for i in items]))

    # ------------------------------------------------------------------
    # Expressions -> bit lists
    # ------------------------------------------------------------------

    @staticmethod
    def _const_bits(text):
        m = _NUM_RE.match(text.replace(" ", ""))
        if m:
            width = int(m.group(1)) if m.group(1) else None
            base = {"b": 2, "o": 8, "d": 10, "h": 16}[m.group(3).lower()]
            digits = re.sub(r"[xXzZ?]", "0", m.group(4).replace("_", ""))
            value = int(digits, base) if digits else 0
        else:
            # A bare number is read as a net name, not a constant: this
            # project's own Verilog writer emits BENCH-style numeric net names
            # (c432's ``118``), and a real netlist never connects an unsized
            # decimal literal to a cell pin.
            return None
        if width is None:
            width = max(1, value.bit_length())
        return [CONST1 if (value >> i) & 1 else CONST0 for i in range(width - 1, -1, -1)]

    def _expr_bits(self, mod, expr, resolve):
        """Expand ``expr`` into a MSB-first list of flattened net names."""
        expr = expr.strip()
        if not expr:
            return []
        consts = self._const_bits(expr)
        if consts is not None:
            return consts
        if expr.startswith("{") and expr.endswith("}"):
            inner = expr[1:-1].strip()
            rep = re.match(r"^(\d+)\s*\{(.*)\}$", inner, re.S)
            if rep:
                return self._expr_bits(mod, "{" + rep.group(2) + "}", resolve) * int(rep.group(1))
            bits = []
            for part in _split_top(inner):
                bits.extend(self._expr_bits(mod, part, resolve))
            return bits
        m = _IDENT_RE.match(expr)
        if not m:
            raise ValueError(f"Unsupported expression in module {mod.name}: {expr!r}")
        name, hi, lo = m.group(1), m.group(2), m.group(3)
        if name in mod.supplies:
            return [CONST1 if mod.supplies[name] else CONST0]
        if hi is None:
            if name in mod.ranges:
                msb, lsb = mod.ranges[name]
                step = -1 if msb >= lsb else 1
                return [resolve(f"{name}[{i}]") for i in range(msb, lsb + step, step)]
            return [resolve(name)]
        hi = int(hi)
        if lo is None:
            return [resolve(f"{name}[{hi}]") if name in mod.ranges else resolve(name)]
        lo = int(lo)
        step = -1 if hi >= lo else 1
        return [resolve(f"{name}[{i}]") for i in range(hi, lo + step, step)]

    def _port_bits(self, mod, port):
        if port in mod.ranges:
            msb, lsb = mod.ranges[port]
            step = -1 if msb >= lsb else 1
            return [f"{port}[{i}]" for i in range(msb, lsb + step, step)]
        return [port]

    # ------------------------------------------------------------------
    # Flattening
    # ------------------------------------------------------------------

    def parse(self, text, filename="design.v"):
        """Parse a gate-level netlist -- or RTL, which is synthesized first.

        RTL (behavioural Verilog, or a multi-file bundle from
        :mod:`pyatpg.core.rtl`) goes through Yosys onto cells this parser
        lowers exactly; any combinational loop the RTL described is then cut
        with a pseudo flip-flop, and both steps are recorded in ``notes``.
        """
        from pyatpg.core import rtl

        if not rtl.needs_synthesis(text):
            return self._parse_netlist(text)
        netlist, notes = rtl.synthesize(text, filename)
        # Checked on the netlist text, before building the circuit, since
        # building it is where an oversized design runs the server out of memory.
        rtl.check_design_size(rtl.count_synthesized_cells(netlist),
                              "After synthesis this design")
        self.notes.extend(notes)
        circuit = self._parse_netlist(netlist)
        rtl.remove_dead_logic(circuit)
        cuts = rtl.break_combinational_loops(circuit)
        if cuts:
            shown = ", ".join(cuts[:8]) + (" ..." if len(cuts) > 8 else "")
            self.notes.append(
                f"The RTL describes {len(cuts)} combinational feedback path(s) with no clock "
                "(state held by an unclocked always block); each was cut with a pseudo "
                f"flip-flop so the value updates once per clock: {shown}"
            )
        return circuit

    def _parse_netlist(self, text):
        from pyatpg.core import rtl

        text = self.clean(text)
        modules = self._parse_modules(text)
        if not modules:
            raise ValueError("No Verilog module found in the netlist.")

        # Modules named like a known cell (e.g. the behavioural ``dff`` that
        # ISCAS netlists ship) are cell models, never the design itself.
        design = {n: m for n, m in modules.items() if _classify_cell(n) is None}
        instantiated = {cell for m in design.values() for cell, _, _ in m.instances}
        tops = [m for n, m in design.items() if n not in instantiated] or list(design.values())
        top = max(tops, key=lambda m: len(m.instances))

        self._gates = []      # (type, out, inputs)
        self._ffs = []        # FlipFlop objects
        self._ff_clocks = []  # (FlipFlop, clock net) for every register with a known clock pin
        self._aliases = []    # (lhs, rhs)
        self._unknown = set()
        self._uniq = 0

        for port in top.ports:
            direction = top.directions.get(port)
            for bit in self._port_bits(top, port):
                if direction == "input":
                    self.circuit.add_input(bit)
                elif direction == "output":
                    self.circuit.add_output(bit)
                elif direction == "inout":
                    raise ValueError(f"inout port {port!r} is not supported by the ATPG model.")

        self._flatten(top, modules, "", {}, depth=0)
        rtl.check_design_size(len(self._gates) + len(self._ffs))

        if self._unknown:
            raise ValueError(
                "Unsupported cell type(s) in the netlist: "
                + ", ".join(sorted(self._unknown))
                + ". Each library cell must be mapped to its exact logic function "
                "for ATPG results to be valid; add a mapping in pyatpg/core/verilog_parser.py."
            )

        for lhs, rhs in self._aliases:
            self._gates.append(("BUF", lhs, [rhs]))
        self._model_logic_clocks()
        self._emit()
        return self.circuit

    def _fresh(self, base):
        self._uniq += 1
        return f"{base}__n{self._uniq}"

    def _flatten(self, mod, modules, prefix, port_map, depth):
        if depth > 64:
            raise ValueError("Module hierarchy is too deep (recursive instantiation?).")

        def resolve(local):
            if local in port_map:
                return port_map[local]
            return prefix + local

        for lhs, rhs in mod.assigns:
            lbits = self._expr_bits(mod, lhs, resolve)
            rbits = self._expr_bits(mod, rhs, resolve) if not rhs.startswith("~") else None
            if rbits is None:
                inner = self._expr_bits(mod, rhs[1:], resolve)
                inv = []
                for b in inner:
                    n = self._fresh(prefix + "inv")
                    self._gates.append(("NOT", n, [b]))
                    inv.append(n)
                rbits = inv
            # Verilog right-aligns (LSB-first) mismatched widths, zero-extending.
            rbits = ([CONST0] * max(0, len(lbits) - len(rbits)) + rbits)[-len(lbits):]
            for lb, rb in zip(lbits, rbits):
                if lb != rb:
                    self._aliases.append((lb, rb))

        for cell, inst, conns in mod.instances:
            path = prefix + inst
            if cell in modules and _classify_cell(cell) is None:
                child = modules[cell]
                child_map = {}
                if isinstance(conns, dict):
                    pairs = [(p, e) for p, e in conns.items()]
                else:
                    pairs = list(zip(child.ports, conns))
                for port, expr in pairs:
                    if port not in child.ports:
                        raise ValueError(f"Module {cell} has no port {port!r} (instance {path}).")
                    formal = self._port_bits(child, port)
                    actual = self._expr_bits(mod, expr, resolve) if expr else []
                    actual = ([None] * max(0, len(formal) - len(actual)) + actual)[-len(formal):]
                    for f, a in zip(formal, actual):
                        if a is None:
                            continue
                        if child.directions.get(port) == "output" and a in _CONSTS:
                            continue  # output tied to a constant: leave it internal
                        child_map[f] = a
                self._flatten(child, modules, path + _HIER_SEP, child_map, depth + 1)
                continue
            self._lower_cell(mod, cell, path, conns, resolve)

    # ------------------------------------------------------------------
    # Cell lowering
    # ------------------------------------------------------------------

    def _bits1(self, mod, expr, resolve):
        bits = self._expr_bits(mod, expr, resolve) if expr else []
        if len(bits) > 1:
            raise ValueError(f"Expected a 1-bit connection, got {expr!r} ({len(bits)} bits)")
        return bits[0] if bits else ""

    def _lower_cell(self, mod, cell, path, conns, resolve):
        kind = _classify_cell(cell)
        if kind is None:
            self._unknown.add(cell)
            return
        family, arg = kind

        if isinstance(conns, list):
            nets = [self._bits1(mod, e, resolve) for e in conns]
            self._lower_positional(cell, family, path, nets)
            return

        pins = {p.upper(): self._bits1(mod, e, resolve) for p, e in conns.items()}

        if family in ("DFF", "SDFF"):
            # Asynchronous set/reset pins are not part of the synchronous
            # model the sequential engine uses. The clock is kept only to
            # tell the real clock apart from one driven by logic (see
            # _model_logic_clocks).
            q = self._ff_q(path, pins.get("Q", ""), pins.get("QN", ""))
            d = pins.get("D", "")
            if family == "SDFF":
                ff = ScanFlipFlop(q, d, pins.get("SI", ""), pins.get("SE", ""))
            else:
                ff = FlipFlop(q, d)
            self._ffs.append(ff)
            clk = next((pins[p] for p in _CLOCK_PINS if pins.get(p)), "")
            if clk:
                self._ff_clocks.append((ff, clk))
            return

        out_names = _ADDER_OUTPUT_PINS if family == "ADD" else _OUTPUT_PINS
        outputs = {p: n for p, n in pins.items() if p in out_names}
        inputs = {p: n for p, n in pins.items() if p not in outputs}

        if family == "TIE":
            for net in outputs.values():
                if net:
                    self._aliases.append((net, CONST1 if arg else CONST0))
            return

        if family == "ADD":
            ins = [inputs[p] for p in sorted(inputs, key=_natural_key)]
            sum_net = outputs.get("SO") or outputs.get("S") or self._fresh(path)
            carry_net = outputs.get("CO") or outputs.get("C1") or self._fresh(path)
            self._xor_tree(path, sum_net, ins, invert=False)
            if len(ins) == 2:
                self._gates.append(("AND", carry_net, ins))
            elif len(ins) == 3:
                a, b, ci = ins
                ab, axb = self._fresh(path), self._fresh(path)
                self._gates.append(("AND", ab, [a, b]))
                self._gates.append(("XOR", axb, [a, b]))
                t = self._fresh(path)
                self._gates.append(("AND", t, [ci, axb]))
                self._gates.append(("OR", carry_net, [ab, t]))
            else:
                self._unknown.add(cell)
            return

        if len(outputs) != 1:
            raise ValueError(f"Cannot determine the output pin of {cell} {path}: pins {sorted(pins)}")
        out = next(iter(outputs.values()))
        if not out:
            return  # output left unconnected: the cell drives nothing
        if family == "MUX":
            sel_pin = next((p for p in inputs if p.startswith("S")), None)
            data = [inputs[p] for p in sorted(inputs, key=_natural_key) if p != sel_pin]
            if sel_pin is None or len(data) != 2:
                self._unknown.add(cell)
                return
            self._mux(path, out, data[0], data[1], inputs[sel_pin], invert=bool(arg))
            return
        if family == "LSEN":
            # Enable level shifter: passes D while enabled, clamps low otherwise.
            en_pin = next((p for p in inputs if p.startswith("EN")), None)
            data_pin = next((p for p in inputs if p != en_pin), None)
            if en_pin is None or data_pin is None:
                self._unknown.add(cell)
                return
            self._gates.append(("AND", out, [inputs[data_pin], inputs[en_pin]]))
            return
        order = sorted(inputs, key=_natural_key)
        if family in ("AO", "AOI", "OA", "OAI"):
            order = _group_pins(order, arg)
            if order is None:
                self._unknown.add(cell)
                return
        ins = [inputs[p] for p in order]
        self._lower_logic(path, family, arg, out, ins, cell)

    def _lower_positional(self, cell, family, path, nets):
        if family in ("DFF", "SDFF"):
            if family == "DFF":
                if len(nets) == 3:
                    clk, q, d = nets
                elif len(nets) == 2:
                    clk, (q, d) = "", nets
                else:
                    raise ValueError(f"Invalid DFF args: {nets}")
                ff = FlipFlop(q, d)
                self._ffs.append(ff)
                if clk:
                    self._ff_clocks.append((ff, clk))
            else:
                if len(nets) == 5:
                    _, q, d, si, se = nets
                elif len(nets) == 4:
                    q, d, si, se = nets
                else:
                    raise ValueError(f"Invalid SDFF args: {nets}")
                self._ffs.append(ScanFlipFlop(q, d, si, se))
            return
        if family in ("NOT", "BUF"):
            # Verilog ``not``/``buf`` may drive several outputs from one input.
            *outs, src = nets
            for o in outs:
                self._gates.append((family, o, [src]))
            return
        if family in ("AND", "NAND", "OR", "NOR", "XOR", "XNOR"):
            out, *ins = nets
            self._lower_logic(path, family, None, out, ins, cell)
            return
        raise ValueError(f"Cell {cell} ({path}) needs named port connections.")

    def _model_logic_clocks(self):
        """Model registers clocked by logic as load-enabled registers.

        The synchronous model clocks every flip-flop on the one system
        clock. That is right for a flip-flop on the clock input (directly or
        through clock buffers/inverters), but a register whose clock pin is
        driven by logic -- the classic Trojan trigger, e.g. Trust-Hub
        s15850-T100's ``DFFNX2 Tg1_Trigger (.CLK(Tg1_Trigger1), .D(1'b1))``
        -- only changes when that logic fires. Clocking it every cycle
        would arm the Trojan on the first clock, and dropping the clock pin
        would leave the trigger logic driving nothing, so it vanished from
        the ATPG model altogether.

        Instead each such register loads D only in cycles where its clock
        net is 1 (Q' = clk ? D : Q), which keeps the trigger logic in the
        circuit, testable, and dormant until it actually fires. Edge
        polarity is not modelled: the register loads once its clock net
        has gone high within a system cycle.
        """
        if not self._ff_clocks:
            return
        drivers = {}
        for gtype, out, ins in self._gates:
            drivers[out] = (gtype, ins)
        inputs = set(self.circuit.inputs)

        def from_clock_input(net):
            seen = set()
            while net not in seen:
                seen.add(net)
                if net in inputs or net in _CONSTS:
                    return True
                gate = drivers.get(net)
                if gate is None:
                    return True  # undriven: becomes an input, like a clock port
                gtype, ins = gate
                if gtype not in ("BUF", "NOT") or len(ins) != 1:
                    return False
                net = ins[0]
            return True

        gated = []
        for ff, clk in self._ff_clocks:
            if from_clock_input(clk):
                continue
            load = self._fresh(f"{ff.q}__load")
            self._mux(ff.q, load, ff.q, ff.d or ff.q, clk, invert=False)
            ff.d = load
            gated.append(ff.q)
            self.circuit.logic_clocks[ff.q] = clk
        if gated:
            shown = ", ".join(gated[:8]) + (" ..." if len(gated) > 8 else "")
            self.notes.append(
                f"{len(gated)} flip-flop(s) are clocked by logic rather than the clock input "
                f"(typical of a Trojan trigger): {shown}. Each is modelled as loading D only "
                "in cycles where its clock net is 1, so the logic driving that clock stays "
                "in the model, is tested, and stays dormant until it fires."
            )

    def _ff_q(self, path, q, qn):
        """A register's Q net; QN becomes NOT(Q) since the model stores one output."""
        if not q:
            q = f"{path}__Q"
        if qn:
            self._gates.append(("NOT", qn, [q]))
        return q

    def _lower_logic(self, path, family, arg, out, ins, cell):
        if family in ("AND", "NAND", "OR", "NOR"):
            self._gates.append((family, out, ins) if len(ins) > 1 else
                               ("BUF" if family in ("AND", "OR") else "NOT", out, ins))
        elif family in ("XOR", "XNOR"):
            self._xor_tree(path, out, ins, invert=family == "XNOR")
        elif family in ("NOT", "BUF"):
            self._gates.append((family, out, ins[:1]))
        elif family in ("AO", "AOI", "OA", "OAI"):
            sizes = [int(c) for c in arg]
            if sum(sizes) != len(ins):
                self._unknown.add(cell)
                return
            inner, outer = ("AND", "OR") if family.startswith("AO") else ("OR", "AND")
            terms, pos = [], 0
            for size in sizes:
                group = ins[pos:pos + size]
                pos += size
                if size == 1:
                    terms.append(group[0])
                else:
                    t = self._fresh(path)
                    self._gates.append((inner, t, group))
                    terms.append(t)
            if family.endswith("I"):
                outer = "NOR" if outer == "OR" else "NAND"
            self._gates.append((outer, out, terms))
        else:
            self._unknown.add(cell)

    def _xor_tree(self, path, out, ins, invert):
        if len(ins) == 1:
            self._gates.append(("NOT" if invert else "BUF", out, ins))
            return
        acc = ins[0]
        for i, b in enumerate(ins[1:]):
            last = i == len(ins) - 2
            n = out if last and not invert else self._fresh(path)
            self._gates.append(("XOR", n, [acc, b]))
            acc = n
        if invert:
            self._gates.append(("NOT", out, [acc]))

    def _mux(self, path, out, d0, d1, sel, invert):
        nsel, t0, t1 = self._fresh(path), self._fresh(path), self._fresh(path)
        self._gates.append(("NOT", nsel, [sel]))
        self._gates.append(("AND", t0, [d0, nsel]))
        self._gates.append(("AND", t1, [d1, sel]))
        self._gates.append(("NOR" if invert else "OR", out, [t0, t1]))

    # ------------------------------------------------------------------
    # Constant folding and emission
    # ------------------------------------------------------------------

    def _emit(self):
        consts = dict(_CONSTS)
        gates = {}
        for gtype, out, ins in self._gates:
            if out in _CONSTS:
                continue  # a cell output wired straight to a tie-off net
            if out in gates:
                raise ValueError(f"Net {out!r} is driven by more than one cell.")
            gates[out] = [gtype, list(ins)]
        # Aliased/tied outputs of Verilog ``assign x = 1'b0`` arrive as BUFs of
        # a constant and fold like everything else.

        fanout = {}
        for out, (_, ins) in gates.items():
            for i in ins:
                fanout.setdefault(i, []).append(out)

        work = [n for n in consts]
        while work:
            net = work.pop()
            for out in fanout.get(net, ()):
                if out not in gates:
                    continue
                folded = _fold(gates[out], consts)
                if folded is None:
                    continue
                if isinstance(folded, int):
                    consts[out] = folded
                    del gates[out]
                    work.append(out)
                else:
                    gates[out] = folded

        for out, (gtype, ins) in gates.items():
            if gtype == "XNOR":  # produced by folding an XOR with a constant 1 input
                x = self._fresh(out)
                self.circuit.add_gate("XOR", x, ins)
                self.circuit.add_gate("NOT", out, [x])
            else:
                self.circuit.add_gate(gtype, out, ins)

        needed = {}
        for ff in self._ffs:
            for attr in ("d", "si", "se"):
                v = getattr(ff, attr, None)
                if v in consts:
                    # Verilog literals are not valid net names downstream
                    # (they surfaced as an output called "1'b1").
                    name = TIE_PREFIX + str(consts[v]) if v in _CONSTS else v
                    setattr(ff, attr, name)
                    needed[name] = consts[v]
            self.circuit.add_flipflop(ff)

        dropped = sorted(o for o in self.circuit.outputs if o in consts)
        for o in dropped:
            self.circuit.outputs.discard(o)
        if dropped:
            shown = ", ".join(dropped[:10]) + (" ..." if len(dropped) > 10 else "")
            self.notes.append(
                f"{len(dropped)} primary output(s) are tied to a constant and carry no "
                f"testable logic, so they were left out of the output list: {shown}"
            )
        if needed:
            # A flip-flop pin tied to a constant: drive it from a tie-off net
            # built out of a primary input, which keeps the logic exact.
            for net in sorted(needed):
                self._tie_net(net, needed[net])
            self.notes.append(
                "Some flip-flop D/SI/SE pins are tied to constants; they are driven "
                "by tie-off logic (x AND NOT x / x OR NOT x) on a dedicated input."
            )

    def _tie_net(self, net, value):
        src = "__tie_src"
        if src not in self.circuit.inputs:
            self.circuit.add_input(src)
            self.circuit.add_gate("NOT", "__tie_src_n", [src])
        self.circuit.add_gate("OR" if value else "AND", net, [src, "__tie_src_n"])


def _fold(gate, consts):
    """Simplify ``gate`` given known-constant inputs.

    Returns an int if the output became constant, a new ``[type, inputs]`` if
    the gate simplified, or None if nothing changed.
    """
    gtype, ins = gate
    known = [consts[i] for i in ins if i in consts]
    if not known:
        return None
    rest = [i for i in ins if i not in consts]
    if gtype in ("BUF", "NOT"):
        return known[0] ^ (gtype == "NOT")
    if gtype in ("AND", "NAND", "OR", "NOR"):
        dominant = 0 if gtype in ("AND", "NAND") else 1
        inverted = gtype in ("NAND", "NOR")
        if dominant in known:
            return dominant ^ inverted
        if not rest:
            return (1 - dominant) ^ inverted
        if len(rest) == 1:
            return ["NOT" if inverted else "BUF", rest]
        return [gtype, rest]
    if gtype in ("XOR", "XNOR"):
        parity = (sum(known) + (gtype == "XNOR")) % 2
        if not rest:
            return parity
        if len(rest) == 1:
            return ["NOT" if parity else "BUF", rest]
        return ["XNOR" if parity else "XOR", rest]
    return None


# ----------------------------------------------------------------------
# Cell classification
# ----------------------------------------------------------------------

def _group_pins(pins, sizes):
    """Order an AO/OA cell's input pins so consecutive runs form its groups.

    SAED numbers every input (IN1..IN6) in group order; other libraries
    letter the groups (GSCLib A0 A1 B0, Nangate A B1 B2), where the letter
    identifies the group and the group order in the name need not match.
    Returns None if the pins cannot be matched to the group sizes.
    """
    sizes = [int(c) for c in sizes]
    letters = [re.sub(r"\d+$", "", p) for p in pins]
    if len(set(letters)) == 1:
        return pins if len(pins) == sum(sizes) else None
    groups = {}
    for p, letter in zip(pins, letters):
        groups.setdefault(letter, []).append(p)
    if sorted(len(g) for g in groups.values()) != sorted(sizes):
        return None
    ordered = []
    remaining = dict(groups)
    for size in sizes:
        letter = next(k for k in sorted(remaining) if len(remaining[k]) == size)
        ordered.extend(remaining.pop(letter))
    return ordered


_LOGIC_RE = re.compile(r"^(NAND|NOR|AND|OR|XNOR|XOR)(\d)")
_AOI_RE = re.compile(r"^(AOI|OAI|AO|OA)(\d{2,4})")


def _classify_cell(name):
    """Map a cell name to ``(family, arg)``, or None if it is not a known cell.

    Families: AND/NAND/OR/NOR/XOR/XNOR/NOT/BUF, AO/AOI/OA/OAI (arg = the group
    sizes, e.g. "221"), MUX (arg = inverting), ADD (half/full adders), LSEN
    (enable level shifter), TIE (arg = value), DFF, SDFF.
    """
    low = name.lower()
    if low in _PRIMITIVES:
        return _PRIMITIVES[low], None
    if low in ("dff", "delta"):
        return "DFF", None
    if low == "sdff":
        return "SDFF", None

    up = name.upper()
    if up.startswith("SDFF"):
        return "SDFF", None
    if up.startswith("DFF"):
        return "DFF", None
    if up.startswith("TIEH") or up.startswith("TIEHI"):
        return "TIE", 1
    if up.startswith("TIEL") or up.startswith("TIELO"):
        return "TIE", 0
    if up.startswith(("INV", "IBUFF", "CLKINV")):
        return "NOT", None
    if up.startswith(("LSDN", "LSUP")):  # level shifters, optionally with enable
        return ("LSEN" if "EN" in up[4:] else "BUF"), None
    if up.startswith(("NBUFF", "BUF", "CLKBUF", "DELLN", "DLY")):
        return "BUF", None
    if up.startswith("ISOLAND"):
        return "AND", None
    if up.startswith("ISOLOR"):
        return "OR", None
    if up.startswith(("MUX21I", "MXI2")):
        return "MUX", True
    if up.startswith(("MUX21", "MX2")):
        return "MUX", False
    if up.startswith(("HADD", "FADD", "ADDH", "ADDF")):
        return "ADD", None
    m = _AOI_RE.match(up)
    if m:
        return m.group(1), m.group(2)
    m = _LOGIC_RE.match(up)
    if m:
        return m.group(1), None
    return None
