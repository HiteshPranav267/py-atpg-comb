"""End-to-end coverage of run_via_api.py's orchestration logic against a
real, locally-generated trojan dataset (no server needed to build the
dataset itself -- pyatpg.trojan is used directly here the same way the
*deployed backend* uses it) and a stub ApiClient standing in for the
network calls. This is what actually proves the API-response shapes this
script expects (RankingResult.from_dict, MetadataAdapter.parse_json, the
ATPG JSON's runs/comparison structure) still line up with the real
domain objects, without needing a live server in the test run.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pyatpg.trojan.generator import TrojanDatasetGenerator
from pyatpg.trojan.inserter import TrojanInserter
from pyatpg.trojan.strategy import RandomStrategy

import run_via_api as cli  # noqa: E402

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks"


class _StubApiClient:
    """Duck-types ApiClient without any network: trojan generation still
    runs for real (locally, mirroring what the deployed backend would
    produce and zip up), while ATPG/feature/ranking calls return
    deterministic canned responses."""

    def __init__(self, dataset_root: Path):
        self.dataset_root = dataset_root
        self.calls = []

    def health(self):
        return True

    def generate_trojan_dataset(self, circuit_path, upload_name, *, variants, strategy, seed=None,
                                 verify_activation=False):
        import io
        import shutil
        import zipfile

        self.calls.append(("generate", upload_name, strategy))
        name = upload_name.rsplit(".", 1)[0]
        work_dir = self.dataset_root / "_gen_work"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)
        staged = work_dir / upload_name
        staged.write_text(circuit_path.read_text(encoding="utf-8"), encoding="utf-8")

        generator = TrojanDatasetGenerator(
            inserter=TrojanInserter(strategy=RandomStrategy(seed=seed)), output_dir=str(work_dir / "out"),
        )
        generator.generate(circuit_path=str(staged), variants=variants)

        out_dir = work_dir / "out" / name
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for path in out_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, Path(name) / path.relative_to(out_dir))
        return buffer.getvalue()

    def run_atpg(self, circuit_path, *, mode, patterns=3000, use_llm=True, backtrack_limit=None, **llm_kwargs):
        self.calls.append(("atpg", circuit_path.name))
        return {
            "job_id": "stub-job",
            "mode": mode,
            "runs": {"non_targeted": {"results": {"fault_coverage": 87.5, "pattern_count": 12}}},
            "comparison": None,
            "notes": [],
            "sequential": None,
        }

    def extract_features(self, circuit_path):
        self.calls.append(("extract", circuit_path.name))
        return {"circuit_name": circuit_path.stem, "gate_features": [], "atpg_coverage": None,
                "extraction_time_ms": 1.0}

    def run_ranking(self, feature_set, ranker_name, **llm_kwargs):
        self.calls.append(("rank", ranker_name))
        return {
            "circuit_name": feature_set["circuit_name"], "ranker_name": ranker_name,
            "execution_time": 0.01,
            # Deliberately wrong guess (an id that doesn't exist) so the
            # evaluation math has something real to compute against ground
            # truth rather than trivially matching everything.
            "ranked_gates": [{"gate_id": "not_a_real_gate", "score": 0.9, "rank": 1, "reason": None}],
        }


@pytest.fixture
def args_namespace(tmp_path):
    import argparse
    return argparse.Namespace(
        strategies=["random"], variants_per_strategy=3, seed=42, verify_activation=False,
        mode="non_targeted", patterns=1000, backtrack_limit=None, no_llm=False, no_ranking=False,
        ranker="ScoapRanker", k=3,
    )


class TestEvaluateVariant:
    def test_evaluates_against_real_metadata(self, tmp_path):
        # Build one real variant directly (not through the stub client) so
        # metadata.json is genuine ground truth, then check _evaluate_variant
        # correctly threads a (stubbed) ranking result through Evaluator.
        out_dir = tmp_path / "gen"
        generator = TrojanDatasetGenerator(
            inserter=TrojanInserter(strategy=RandomStrategy(seed=7)), output_dir=str(out_dir),
        )
        circuit_path = tmp_path / "c432.bench"
        circuit_path.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
        generated = generator.generate(circuit_path=str(circuit_path), variants=2)
        assert generated >= 1
        variant_dir = next((out_dir / "c432").glob("trojan_*"))

        client = _StubApiClient(tmp_path)
        row = cli._evaluate_variant(client, variant_dir, "ScoapRanker", 3, {})

        assert row["ranker_name"] == "ScoapRanker"
        assert 0.0 <= row["top1_accuracy"] <= 1.0
        assert ("extract", "circuit.v") in client.calls
        assert ("rank", "ScoapRanker") in client.calls

    def test_raises_when_variant_is_incomplete(self, tmp_path):
        variant_dir = tmp_path / "trojan_0001"
        variant_dir.mkdir()
        (variant_dir / "circuit.v").write_text("module x; endmodule")
        # metadata.json deliberately missing
        client = _StubApiClient(tmp_path)
        with pytest.raises(ValueError, match="metadata.json"):
            cli._evaluate_variant(client, variant_dir, "ScoapRanker", 3, {})


class TestRunOneCircuit:
    def test_full_flow_produces_atpg_and_ranking_rows(self, tmp_path, args_namespace):
        circuit = tmp_path / "c432.bench"
        circuit.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
        output_dir = tmp_path / "out"
        client = _StubApiClient(output_dir)

        summary = cli._run_one_circuit(circuit, client, output_dir, args_namespace, {}, log=lambda *a: None)

        assert summary["variants_generated"] >= 1
        assert summary["atpg_rows"] == summary["variants_generated"] + 1  # + the clean circuit
        assert summary["ranking_rows"] == summary["variants_generated"]

        master_atpg = (output_dir / "master_atpg_summary.csv").read_text(encoding="utf-8")
        assert "clean" in master_atpg
        assert "non_targeted_fault_coverage" in master_atpg
        assert (output_dir / "master_ranking_summary.csv").exists()
        assert (output_dir / "c432" / "atpg" / "clean.json").exists()

    def test_no_ranking_flag_skips_the_ranking_step(self, tmp_path, args_namespace):
        args_namespace.no_ranking = True
        circuit = tmp_path / "c432.bench"
        circuit.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
        output_dir = tmp_path / "out"
        client = _StubApiClient(output_dir)

        summary = cli._run_one_circuit(circuit, client, output_dir, args_namespace, {}, log=lambda *a: None)

        assert summary["ranking_rows"] == 0
        assert not any(call[0] == "rank" for call in client.calls)

    def test_llm_kwargs_are_forwarded_to_every_server_call(self, tmp_path, args_namespace):
        circuit = tmp_path / "c432.bench"
        circuit.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
        output_dir = tmp_path / "out"

        class RecordingClient(_StubApiClient):
            def run_atpg(self, circuit_path, *, mode, patterns=3000, use_llm=True, backtrack_limit=None,
                         **llm_kwargs):
                assert llm_kwargs == {"api_key": "AIzaFake"}
                return super().run_atpg(circuit_path, mode=mode, patterns=patterns, use_llm=use_llm,
                                         backtrack_limit=backtrack_limit, **llm_kwargs)

            def run_ranking(self, feature_set, ranker_name, **llm_kwargs):
                assert llm_kwargs == {"api_key": "AIzaFake"}
                return super().run_ranking(feature_set, ranker_name, **llm_kwargs)

        client = RecordingClient(output_dir)
        cli._run_one_circuit(circuit, client, output_dir, args_namespace, {"api_key": "AIzaFake"}, log=lambda *a: None)


class TestCliArgparse:
    def test_help_does_not_crash(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(["--help"])
        assert exc_info.value.code == 0

    def test_missing_circuits_returns_exit_code_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rc = cli.main(["--circuits", str(tmp_path / "does_not_exist"), "--api-url", "https://unused"])
        assert rc == 1
