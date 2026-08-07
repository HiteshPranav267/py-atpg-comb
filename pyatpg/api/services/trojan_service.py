"""Trojan dataset generation service."""
import os
import shutil
import tempfile

from pyatpg.trojan.generator import TrojanDatasetGenerator
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import RandomStrategy, RareNodeStrategy

class TrojanService:
    @staticmethod
    def generate_dataset(
        verilog_text: str,
        circuit_name: str = "circuit",
        variants: int = 10,
        strategy: str = "random",
    ) -> dict:
        if strategy == "rare_node":
            strat = RareNodeStrategy()
        else:
            strat = RandomStrategy()

        inserter = TrojanInserter(strategy=strat)
        output_dir = "datasets"
        generator = TrojanDatasetGenerator(inserter=inserter, output_dir=output_dir)

        # TrojanDatasetGenerator reads circuits from disk and derives the
        # dataset directory name from the file's basename, so the temp file
        # must be named exactly "<circuit_name>.v" to keep that name clean.
        tmp_dir = tempfile.mkdtemp(prefix="pyatpg_trojan_")
        tmp_path = os.path.join(tmp_dir, f"{circuit_name}.v")
        try:
            with open(tmp_path, 'w') as f:
                f.write(verilog_text)
            generator.generate(circuit_path=tmp_path, variants=variants)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return {
            "circuit": circuit_name,
            "variants_generated": variants,
            "strategy": strategy,
            "output_dir": os.path.join(output_dir, circuit_name),
        }
