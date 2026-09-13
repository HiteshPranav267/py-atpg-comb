"""End-to-end coverage of the full generate -> ATPG -> rank pipeline.

Uses the real Atalanta binary and the real trojan generator (both already
required by the rest of the test suite) but a non-LLM ranker, so this
needs no API key and no network -- it's exercising the orchestration
(dataset layout, staging, manifests, master CSVs), not any specific
ranker's accuracy.
"""
from pathlib import Path

import pytest

from pyatpg.atpg import AnalysisMode, AtpgAnalysisService
from pyatpg.automation.research_pipeline import GenerationConfig, run_research_suite
from pyatpg.automation.retry import RetryPolicy
from pyatpg.ranking import ScoapRanker

BENCHMARKS = Path(__file__).resolve().parent.parent / "benchmarks"


def _fast_policy() -> RetryPolicy:
    return RetryPolicy(max_attempts=1, base_delay=0.0, max_delay=0.0)


def _atpg_run_one(path: Path):
    service = AtpgAnalysisService()
    return service.run(
        source_text=path.read_text(encoding="utf-8"), filename=path.name,
        mode=AnalysisMode.NON_TARGETED, use_llm=False,
    )


@pytest.fixture
def clean_circuit(tmp_path):
    # c432 is large enough that every insertion strategy reliably finds a
    # valid target; c17 is too small and would make this test flaky.
    dst = tmp_path / "c432.bench"
    dst.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
    return dst


class TestRunResearchSuite:
    def test_full_pipeline_produces_expected_outputs(self, tmp_path, clean_circuit):
        output_dir = tmp_path / "out"
        gen_config = GenerationConfig(strategies=("random",), variants_per_strategy=2, seed=42)

        counts = run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )

        assert counts["done"] == 1
        assert counts["failed"] == 0

        circuit_dir = output_dir / "c432"
        assert (circuit_dir / "dataset" / ".generation.json").exists()
        variant_dirs = list((circuit_dir / "dataset" / "c432__random").glob("trojan_*"))
        assert len(variant_dirs) >= 1  # generator may legitimately skip some

        staged = list((circuit_dir / "staged").iterdir())
        assert any("clean" in p.name for p in staged)
        assert len(staged) == 1 + len(variant_dirs)  # clean + every variant, uniquely named

        atpg_summary = (circuit_dir / "atpg" / "summary.csv").read_text(encoding="utf-8")
        assert "clean" in atpg_summary
        assert (circuit_dir / "ranking" / f"benchmark_{ScoapRanker().name}.json").exists()

        master_atpg = (output_dir / "master_atpg_summary.csv").read_text(encoding="utf-8")
        assert "c432" in master_atpg
        assert (output_dir / "master_ranking_summary.csv").exists()

    def test_rerun_skips_dataset_regeneration(self, tmp_path, clean_circuit, monkeypatch):
        output_dir = tmp_path / "out"
        gen_config = GenerationConfig(strategies=("random",), variants_per_strategy=2, seed=42)

        run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )

        import pyatpg.automation.research_pipeline as rp

        def boom(*a, **k):
            raise AssertionError("dataset should not have been regenerated")

        monkeypatch.setattr(rp, "_generate_dataset", boom)

        counts = run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )
        assert counts["done"] == 1  # still reported done via the untouched, already-complete dataset

    def test_force_regenerates_and_reruns_everything(self, tmp_path, clean_circuit):
        output_dir = tmp_path / "out"
        gen_config = GenerationConfig(strategies=("random",), variants_per_strategy=2, seed=42)

        run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )
        counts = run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, force=True, log=lambda *a: None,
        )
        assert counts["done"] == 1

    def test_master_csv_replaces_a_circuits_rows_rather_than_duplicating(self, tmp_path, clean_circuit):
        import csv

        output_dir = tmp_path / "out"
        gen_config = GenerationConfig(strategies=("random",), variants_per_strategy=2, seed=42)

        run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )
        run_research_suite(
            [clean_circuit], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, force=True, log=lambda *a: None,
        )

        with (output_dir / "master_atpg_summary.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        clean_rows = [r for r in rows if r["circuit"].endswith("__clean.bench")]
        assert len(clean_rows) == 1  # not duplicated across the two runs

    def test_multiple_circuits_each_get_their_own_subtree(self, tmp_path):
        c432 = tmp_path / "c432.bench"
        c432.write_text((BENCHMARKS / "c432.bench").read_text(encoding="utf-8"))
        c880 = tmp_path / "c880.bench"
        c880.write_text((BENCHMARKS / "c880.bench").read_text(encoding="utf-8"))

        output_dir = tmp_path / "out"
        gen_config = GenerationConfig(strategies=("random",), variants_per_strategy=1, seed=42)

        counts = run_research_suite(
            [c432, c880], output_dir, gen_config,
            _atpg_run_one, _fast_policy(), lambda: ScoapRanker(), _fast_policy(),
            ranker_k=3, log=lambda *a: None,
        )
        assert counts["done"] == 2
        assert (output_dir / "c432" / "dataset").exists()
        assert (output_dir / "c880" / "dataset").exists()

        with (output_dir / "master_atpg_summary.csv").open(newline="", encoding="utf-8") as handle:
            import csv
            source_circuits = {r["source_circuit"] for r in csv.DictReader(handle)}
        assert source_circuits == {"c432.bench", "c880.bench"}
