import os
import json
import csv
from typing import List, Dict, Any
from pyatpg.core.netlist import NetlistParser
from pyatpg.core.verilog_parser import VerilogParser
from pyatpg.analysis.adapters import AtalantaAdapter, MetadataAdapter
from pyatpg.analysis.feature_extractor import FeatureExtractor
from pyatpg.ranking.base import GateRanker
from pyatpg.evaluation.evaluator import Evaluator
from pyatpg.evaluation.models import EvaluationResult


class BenchmarkRunner:
    """Executes a GateRanker across an entire Trojan dataset and aggregates metrics."""

    def __init__(self, dataset_dir: str, ranker: GateRanker, output_dir: str = "results", k: int = 5):
        self.dataset_dir = dataset_dir
        self.ranker = ranker
        self.output_dir = output_dir
        self.evaluator = Evaluator(k=k)
        self.extractor = FeatureExtractor()

    def run(self) -> List[EvaluationResult]:
        """Runs the benchmark and returns the evaluation results."""
        results: List[EvaluationResult] = []
        os.makedirs(self.output_dir, exist_ok=True)

        if not os.path.exists(self.dataset_dir):
            print(f"[Benchmark] Dataset directory {self.dataset_dir} not found.")
            return []

        # Iterate through circuits (e.g. c17, c432)
        for circuit_name in os.listdir(self.dataset_dir):
            circuit_dir = os.path.join(self.dataset_dir, circuit_name)
            if not os.path.isdir(circuit_dir):
                continue

            # Iterate through trojan variants
            for variant_dir in os.listdir(circuit_dir):
                if not variant_dir.startswith("trojan_"):
                    continue

                full_variant_dir = os.path.join(circuit_dir, variant_dir)
                v_path = os.path.join(full_variant_dir, "circuit.v")
                meta_path = os.path.join(full_variant_dir, "metadata.json")

                if not os.path.exists(v_path) or not os.path.exists(meta_path):
                    continue

                print(f"[Benchmark] Evaluating {circuit_name} / {variant_dir} with {self.ranker.name}...")

                # 1. Load Circuit
                with open(v_path, 'r') as f:
                    text = f.read()
                circuit = VerilogParser().parse(text)
                circuit.name = circuit_name

                # 2. Load Ground Truth
                ground_truth = MetadataAdapter.parse_json(meta_path)

                # 3. Load ATPG (Stubbed for now, normally you'd run Atalanta or load logs)
                # In a real benchmark, you might run ATPG here or assume it's pre-computed
                atpg_result = AtalantaAdapter.parse_report("dummy.txt")

                # 4. Extract FeatureSet
                feature_set = self.extractor.extract(
                    circuit=circuit,
                    atpg_result=atpg_result,
                    ground_truth=ground_truth
                )

                # 5. Rank
                ranking = self.ranker.rank(feature_set)

                # 6. Evaluate
                eval_result = self.evaluator.evaluate(ranking, ground_truth)
                results.append(eval_result)

        self._export_results(results)
        return results

    def _export_results(self, results: List[EvaluationResult]):
        if not results:
            return

        json_path = os.path.join(self.output_dir, f"benchmark_{self.ranker.name}.json")
        csv_path = os.path.join(self.output_dir, f"benchmark_{self.ranker.name}.csv")

        # JSON
        with open(json_path, 'w') as f:
            json.dump([r.to_dict() for r in results], f, indent=2)

        # CSV
        fieldnames = list(results[0].to_dict().keys())
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow(r.to_dict())
                
        print(f"[Benchmark] Results exported to {json_path} and {csv_path}")
