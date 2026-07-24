"""Trojan dataset generation service."""
from pyatpg.trojan.generator import TrojanDatasetGenerator
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import RandomStrategy, RareNodeStrategy

class TrojanService:
    @staticmethod
    def generate_dataset(circuit_path: str, variants: int = 10, strategy: str = "random") -> dict:
        if strategy == "rare_node":
            strat = RareNodeStrategy()
        else:
            strat = RandomStrategy()
            
        inserter = TrojanInserter(strategy=strat)
        generator = TrojanDatasetGenerator(inserter=inserter, output_dir="datasets")
        generator.generate(circuit_path=circuit_path, variants=variants)
        
        return {
            "circuit": circuit_path,
            "variants_generated": variants,
            "strategy": strategy,
            "output_dir": "datasets"
        }
