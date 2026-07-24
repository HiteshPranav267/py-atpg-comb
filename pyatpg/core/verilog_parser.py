import re
from pyatpg.core.netlist import Circuit, FlipFlop, ScanFlipFlop


class VerilogParser:
    def __init__(self):
        self.circuit = Circuit()

    def clean(self, text):
        """Remove comments."""
        text = re.sub(r"//.*", "", text)
        return text

    def _join_continuation_lines(self, text):
        """Join multi-line statements (lines ending with comma or without semicolon)."""
        lines = text.splitlines()
        joined = []
        current = ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current:
                    joined.append(current)
                    current = ""
                continue
            current = (current + " " + stripped) if current else stripped
            if stripped.endswith(";") or stripped.endswith(")"):
                joined.append(current)
                current = ""
            # Lines starting with keywords that don't need continuation
            elif stripped.startswith(("module", "endmodule", "reg", "wire",
                                     "always")):
                joined.append(current)
                current = ""
        if current:
            joined.append(current)
        return joined

    def _extract_main_module(self, text):
        """Extract only the main (last) module, skipping cell definitions."""
        modules = []
        current = []
        in_module = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("module"):
                in_module = True
                current = [line]
            elif stripped == "endmodule":
                current.append(line)
                if in_module:
                    modules.append("\n".join(current))
                in_module = False
                current = []
            elif in_module:
                current.append(line)

        if not modules:
            return text
        # Return the largest module (the main one)
        return max(modules, key=len)

    def parse(self, text):
        text = self.clean(text)
        text = self._extract_main_module(text)
        lines = self._join_continuation_lines(text)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # --------------------------
            # INPUT
            # --------------------------
            if line.startswith("input"):
                names = self._extract_names(line)
                for n in names:
                    self.circuit.add_input(n)

            # --------------------------
            # OUTPUT
            # --------------------------
            elif line.startswith("output"):
                names = self._extract_names(line)
                for n in names:
                    self.circuit.add_output(n)
                    
            # --------------------------
            # SKIPPED KEYWORDS
            # --------------------------
            elif line.startswith(("module", "endmodule", "wire", "reg", "assign")):
                continue

            # --------------------------
            # GATES / DFF
            # --------------------------
            else:
                self._parse_gate(line)

        return self.circuit

    # -------------------------------------------------

    def _extract_names(self, line):
        line = line.replace("input", "")
        line = line.replace("output", "")
        line = line.replace(";", "")
        return [x.strip() for x in line.split(",") if x.strip()]

    def _parse_gate(self, line):
        if "(" not in line or ")" not in line:
            return

        tokens = line.split()
        if len(tokens) < 2:
            return

        gate_type = tokens[0].lower()

        args = re.search(r"\((.*)\)", line)
        if not args:
            return

        # Handle both positional (A, B) and named (.A(A), .B(B)) mapping
        raw_parts = [x.strip() for x in args.group(1).split(",")]
        parts = []
        named_ports = {}
        for p in raw_parts:
            # If it's a named port like .D (n_123) or .Q(G33)
            match = re.match(r"\.([A-Za-z0-9_]+)\s*\((.*?)\)", p)
            if match:
                port_name = match.group(1).upper()
                net_name = match.group(2).strip()
                named_ports[port_name] = net_name
                parts.append(net_name)
            else:
                parts.append(p)

        # --------------------------
        # DFF and SDFF
        # --------------------------
        if gate_type == "dff":
            if named_ports:
                q = named_ports.get('Q', '')
                d = named_ports.get('D', '')
            else:
                # Format: dff(CK,Q,D)
                if len(parts) == 3:
                    _, q, d = parts
                elif len(parts) == 2:
                    q, d = parts
                else:
                    raise ValueError(f"Invalid DFF args: {parts}")

            self.circuit.add_flipflop(FlipFlop(q, d))
            return
        elif gate_type == "sdff" or gate_type.startswith("sdff"):
            # If using named ports (e.g. Cadence Genus output)
            if named_ports:
                q = named_ports.get('Q', '')
                d = named_ports.get('D', '')
                si = named_ports.get('SI', '')
                se = named_ports.get('SE', '')
                if not q or not d or not si or not se:
                    # try alternative names like QN, etc. if needed, but basic should be there
                    pass
            else:
                # Format: sdff(CK, Q, D, SI, SE) or sdff(Q, D, SI, SE)
                if len(parts) == 5:
                    _, q, d, si, se = parts
                elif len(parts) == 4:
                    q, d, si, se = parts
                else:
                    raise ValueError(f"Invalid SDFF args: {parts}")
            
            self.circuit.add_flipflop(ScanFlipFlop(q, d, si, se))
            self.circuit.scan_inputs.add(si)
            self.circuit.scan_enables.add(se)
            self.circuit.scan_outputs.add(q)
            return

        # --------------------------
        # Logic gate
        # --------------------------
        if named_ports:
            # Look for known output pin names
            output_pins = ['Q', 'QN', 'ZN', 'Y', 'Z', 'O', 'CO', 'OUT']
            output = None
            for op in output_pins:
                if op in named_ports:
                    output = named_ports[op]
                    break
            
            # Fallback if no output pin is explicitly found
            if not output:
                for pin in named_ports:
                    if pin.endswith('N') or pin.endswith('Z') or pin.endswith('Y') or pin == 'Q':
                        output = named_ports[pin]
                        break
                if not output:
                    output = list(named_ports.values())[-1]
            
            # Inputs are all nets except the output net
            inputs = [net for pin, net in named_ports.items() if net != output]
        else:
            output = str(parts[0]) if parts else ""
            inputs = [str(x) for x in parts[1:]] if len(parts) > 1 else []

        if gate_type.startswith("and"):
            gtype = "AND"
        elif gate_type.startswith("nand"):
            gtype = "NAND"
        elif gate_type.startswith("nor"):
            gtype = "NOR"
        elif gate_type.startswith("or"):
            gtype = "OR"
        elif gate_type.startswith("not") or gate_type.startswith("inv"):
            gtype = "NOT"
        elif gate_type.startswith("xor") or gate_type.startswith("xnor"):
            gtype = "XOR"
        elif gate_type.startswith("buf") or gate_type.startswith("buff"):
            gtype = "BUF"
        elif gate_type.startswith("aoi") or gate_type.startswith("ao"):
            gtype = "NOR"
        elif gate_type.startswith("oai") or gate_type.startswith("oa"):
            gtype = "NAND"
        elif gate_type.startswith("mux") or gate_type.startswith("mx"):
            gtype = "OR"
        else:
            # Keep the gate in the graph as a BUF
            gtype = "BUF"

        if output:
            self.circuit.add_gate(gtype, output, inputs)
