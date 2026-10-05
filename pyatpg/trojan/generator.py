import os
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.core.netlist import NetlistParser
from pyatpg.trojan.inserter import TrojanInserter


def _write_netlist(path: str, text: str) -> None:
    """Write a netlist with Unix line endings, whatever the host platform.

    Atalanta's parser treats a stray carriage return as part of the token and
    fails with "Gate type ... is not valid", so a dataset generated on Windows
    in default text mode would be unreadable by the Linux build the deployment
    runs. Mirrors JobWorkspace.write_text, which learned this the same way.
    """
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)


class TrojanDatasetGenerator:
    """Generates a dataset of hardware trojans by wrapping a TrojanInserter."""
    
    def __init__(self, inserter: TrojanInserter, output_dir: str = "datasets"):
        self.inserter = inserter
        self.output_dir = output_dir

    def _parse_circuit(self, circuit_path: str):
        if not os.path.exists(circuit_path):
            raise FileNotFoundError(f"Base circuit not found: {circuit_path}")

        with open(circuit_path, 'r', encoding='utf-8') as f:
            # Both parsers split on newlines and neither strips a lone "\r",
            # so normalise here rather than relying on the platform's text-mode
            # translation, which does not touch a bare CR.
            text = f.read().replace('\r\n', '\n').replace('\r', '\n')

        if circuit_path.endswith(('.v', '.sv', '.verilog')):
            parser = VerilogParser()
        elif circuit_path.endswith('.bench'):
            parser = NetlistParser()
        else:
            raise ValueError(
                f"Unsupported base circuit format: {os.path.basename(circuit_path)!r}. "
                "Provide a .v, .verilog or .bench file."
            )

        circuit = parser.parse(text)
        self._reject_implausible_parse(circuit, circuit_path)
        return circuit

    @staticmethod
    def _reject_implausible_parse(circuit, circuit_path: str) -> None:
        """Fail on a parse that clearly did not understand the source.

        Neither parser validates its input: the Verilog parser maps any
        unrecognised gate keyword to a BUF, so BENCH text fed to it yields a
        circuit full of gates wired out of the wrong tokens, with no declared
        inputs or outputs and no error raised. That used to happen for every
        .bench upload, because the API wrote the file as ".v" regardless of
        what was uploaded -- producing a complete, exportable dataset of
        meaningless netlists that only failed much later, as an ATPG run with
        no results summary.

        A netlist with no gates, no primary inputs, or no primary outputs is
        unusable for trojan insertion and ATPG whatever the cause, so the
        cheap structural check doubles as format-mismatch detection.
        """
        missing = [
            label
            for label, present in (
                ("gates", circuit.gates),
                ("primary inputs", circuit.inputs),
                ("primary outputs", circuit.outputs),
            )
            if not present
        ]
        if not missing:
            return
        raise ValueError(
            f"Parsed {os.path.basename(circuit_path)!r} but found no "
            f"{', or '.join(missing)}. The file's contents do not look like its "
            f"{os.path.splitext(circuit_path)[1] or 'given'} format -- check that a "
            "BENCH netlist is named .bench and a Verilog netlist .v."
        )

    def generate(self, circuit_path: str, variants: int = 100,
                 num_trigger_inputs: int = 2) -> int:
        """Generate variants of compromised circuits from the base circuit.

        ``num_trigger_inputs`` is how many nets the trigger gate ANDs (ORs,
        etc.) together to form its activation condition. A wider trigger is a
        rarer, harder-to-activate condition -- the joint probability of every
        input landing on its rare value falls with each extra input -- which
        is a direct knob on how stealthy/hard-to-detect the inserted trojan
        is, independent of which insertion strategy picked the nets.

        Returns the number of variants actually written, which can be fewer
        than requested: a strategy may legitimately find no acyclic payload
        target for its trigger picks on a small circuit, and that variant is
        skipped rather than emitted as a netlist the ATPG engine crashes on.
        """
        circuit_name = os.path.basename(circuit_path).split('.')[0]
        base_circuit = self._parse_circuit(circuit_path)
        
        # Directory Structure: datasets/<circuit>/
        ds_dir = os.path.join(self.output_dir, circuit_name)
        clean_dir = os.path.join(ds_dir, "clean")
        os.makedirs(clean_dir, exist_ok=True)
        
        # Save clean version (both Verilog and .bench formats)
        clean_verilog_path = os.path.join(clean_dir, "circuit.v")
        _write_netlist(clean_verilog_path, base_circuit.to_verilog(module_name=circuit_name))

        clean_bench_path = os.path.join(clean_dir, "circuit.bench")
        _write_netlist(clean_bench_path, base_circuit.to_bench())

        print(f"[TrojanDataset] Clean circuit exported to: {clean_verilog_path}")
        
        # Generate Trojans
        generated = 0
        for i in range(1, variants + 1):
            trojan_dir = os.path.join(ds_dir, f"trojan_{i:04d}")

            try:
                # The inserter clones the circuit, so we pass the clean base_circuit repeatedly
                compromised, metadata = self.inserter.insert(
                    circuit=base_circuit,
                    trojan_id=i,
                    num_trigger_inputs=num_trigger_inputs,
                )

                metadata.circuit = circuit_name

                # Created only once insertion has succeeded, so a skipped
                # variant leaves no empty directory to be zipped up and
                # mistaken for a generated one.
                os.makedirs(trojan_dir, exist_ok=True)

                # Export Verilog and .bench
                v_path = os.path.join(trojan_dir, "circuit.v")
                _write_netlist(v_path, compromised.to_verilog(module_name=circuit_name))

                bench_path = os.path.join(trojan_dir, "circuit.bench")
                _write_netlist(bench_path, compromised.to_bench())

                # Export JSON
                j_path = os.path.join(trojan_dir, "metadata.json")
                metadata.save(j_path)
                
                generated += 1
                print(f"[TrojanDataset] Variant {i}/{variants} exported to {trojan_dir}")

            except Exception as e:
                print(f"[TrojanDataset] Error generating variant {i}: {e}")

        if generated < variants:
            print(
                f"[TrojanDataset] {generated}/{variants} variants generated; "
                f"{variants - generated} skipped."
            )
        return generated
