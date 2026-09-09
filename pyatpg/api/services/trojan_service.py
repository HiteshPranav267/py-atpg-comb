"""Trojan dataset generation service."""
import os
import shutil
import tempfile

from pyatpg.trojan.generator import TrojanDatasetGenerator
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import RandomStrategy, RareNodeStrategy, LowScoapStrategy

#: Extensions the dataset generator can parse, and the one assumed when an
#: upload arrives without a usable extension.
_SUPPORTED_SUFFIXES = (".v", ".verilog", ".bench")
_DEFAULT_SUFFIX = ".v"

#: Where generated datasets are written, relative to the working directory.
DEFAULT_OUTPUT_DIR = "datasets"


def _suffix_for(filename: str | None) -> str:
    """The extension to give the temp file the generator parses.

    The generator picks its parser from the extension, so this decides how the
    upload is read. It used to be hardcoded to ".v", which silently ran BENCH
    uploads through the Verilog parser -- that parser maps every unrecognised
    token to a BUF rather than failing, so the result was a full dataset of
    garbage netlists instead of an error.
    """
    suffix = os.path.splitext(filename or "")[1].lower()
    return suffix if suffix in _SUPPORTED_SUFFIXES else _DEFAULT_SUFFIX


class TrojanService:
    @staticmethod
    def generate_dataset(
        source_text: str,
        circuit_name: str = "circuit",
        variants: int = 10,
        strategy: str = "random",
        filename: str | None = None,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        seed: int | None = None,
        verify_activation: bool = False,
    ) -> dict:
        """Generate `variants` trojan-inserted copies of an uploaded circuit.

        Args:
            source_text: the netlist source, Verilog or BENCH.
            circuit_name: name for the dataset directory and module.
            variants: how many trojan variants to attempt.
            strategy: "random", "rare_node" or "low_scoap".
            filename: the original upload's filename. Only its extension is
                used, to choose the parser; without it the text is assumed to
                be Verilog.
            output_dir: root the dataset is written under. Relative to the
                process's working directory by default.
            seed: fixes where the trojans go. Variants within one dataset
                still differ from each other; the same seed reproduces the
                same dataset. None draws from OS entropy.
            verify_activation: run an extra targeted Atalanta job per
                insertion to confirm the trigger is actually reachable and
                observable, silently dropping any variant that isn't (see
                pyatpg.trojan.activation). Off by default: it requires a
                working Atalanta binary and roughly doubles the work per
                variant (one extra ATPG job), and every existing caller of
                this service predates the guarantee existing at all.
        """
        if strategy == "rare_node":
            strat = RareNodeStrategy(seed=seed)
        elif strategy == "low_scoap":
            strat = LowScoapStrategy(seed=seed)
        else:
            strat = RandomStrategy(seed=seed)

        inserter = TrojanInserter(strategy=strat, verify_activation=verify_activation)
        generator = TrojanDatasetGenerator(inserter=inserter, output_dir=output_dir)

        # TrojanDatasetGenerator reads circuits from disk, derives the dataset
        # directory name from the file's basename, and picks its parser from
        # the extension -- so the temp file must be named exactly
        # "<circuit_name><original extension>" for both to come out right.
        tmp_dir = tempfile.mkdtemp(prefix="pyatpg_trojan_")
        tmp_path = os.path.join(tmp_dir, f"{circuit_name}{_suffix_for(filename)}")
        try:
            # newline="" disables translation. A CRLF upload written in
            # default text mode on Windows becomes "\r\r\n" on disk; reading
            # that back leaves a stray carriage return that breaks the Verilog
            # parser's line joining, which silently turned c432's 36 declared
            # inputs into 10 -- a wrong but entirely plausible-looking dataset.
            with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
                f.write(source_text)
            generated = generator.generate(circuit_path=tmp_path, variants=variants)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return {
            "circuit": circuit_name,
            # What was actually written, not what was asked for: a strategy can
            # legitimately skip a variant (no acyclic payload target for its
            # trigger picks), and reporting the request back would claim
            # variants the zip does not contain.
            "variants_generated": generated,
            "variants_requested": variants,
            "strategy": strategy,
            "seed": seed,
            "verify_activation": verify_activation,
            "output_dir": os.path.join(output_dir, circuit_name),
        }
