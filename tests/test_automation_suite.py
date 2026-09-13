"""End-to-end (but fast: no real Atalanta/LLM calls) coverage of the batch
runner -- discovery, resumability across "runs", and retry-then-give-up.
"""
import json
import time

import pytest

from pyatpg.automation.manifest import Manifest
from pyatpg.automation.retry import RetryPolicy
from pyatpg.automation.suite import discover_circuits, run_atpg_suite


class _FakeAnalysis:
    """Minimal stand-in for AtpgAnalysis: only .to_dict() is used."""

    def __init__(self, circuit_name: str, coverage: float):
        self._payload = {
            "job_id": "job-1",
            "mode": "compare",
            "circuit": {"name": circuit_name},
            "runs": {"non_targeted": {"results": {"fault_coverage": coverage, "pattern_count": 10}}},
            "comparison": None,
            "notes": [],
            "sequential": None,
        }

    def to_dict(self):
        return self._payload


class TestDiscoverCircuits:
    def test_finds_files_in_a_directory(self, tmp_path):
        (tmp_path / "c17.bench").write_text("x")
        (tmp_path / "c432.v").write_text("x")
        (tmp_path / "readme.txt").write_text("not a circuit")
        found = discover_circuits([str(tmp_path)])
        assert {p.name for p in found} == {"c17.bench", "c432.v"}

    def test_glob_pattern(self, tmp_path):
        (tmp_path / "c17.bench").write_text("x")
        (tmp_path / "c432.bench").write_text("x")
        found = discover_circuits([str(tmp_path / "*.bench")])
        assert {p.name for p in found} == {"c17.bench", "c432.bench"}

    def test_deduplicates_overlapping_patterns(self, tmp_path):
        f = tmp_path / "c17.bench"
        f.write_text("x")
        found = discover_circuits([str(tmp_path), str(f)])
        assert len(found) == 1

    def test_missing_directory_yields_nothing(self, tmp_path):
        assert discover_circuits([str(tmp_path / "does_not_exist")]) == []


class TestRunAtpgSuite:
    def _policy(self):
        return RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)

    def test_runs_every_circuit_and_writes_outputs(self, tmp_path):
        circuits = []
        for name in ("c17", "c432"):
            f = tmp_path / f"{name}.bench"
            f.write_text(f"# {name}")
            circuits.append(f)

        output_dir = tmp_path / "out"
        counts = run_atpg_suite(
            circuits,
            lambda path: _FakeAnalysis(path.stem, coverage=100.0),
            output_dir, self._policy(), log=lambda *a: None,
        )
        assert counts == {"done": 2, "failed": 0, "skipped": 0}
        assert (output_dir / "c17.json").exists()
        assert (output_dir / "c432.json").exists()
        summary = (output_dir / "summary.csv").read_text()
        assert "c17" in summary and "c432" in summary

    def test_rerun_skips_completed_circuits(self, tmp_path):
        f = tmp_path / "c17.bench"
        f.write_text("# c17")
        output_dir = tmp_path / "out"
        calls = {"n": 0}

        def run_one(path):
            calls["n"] += 1
            return _FakeAnalysis(path.stem, coverage=100.0)

        run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)
        counts = run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)

        assert calls["n"] == 1  # second run never called run_one again
        assert counts == {"done": 0, "failed": 0, "skipped": 1}

    def test_editing_the_circuit_forces_a_rerun(self, tmp_path):
        f = tmp_path / "c17.bench"
        f.write_text("# c17 v1")
        output_dir = tmp_path / "out"
        calls = {"n": 0}

        def run_one(path):
            calls["n"] += 1
            return _FakeAnalysis(path.stem, coverage=100.0)

        run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)
        time.sleep(0.01)
        f.write_text("# c17 v2, edited by a friend")
        run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)

        assert calls["n"] == 2

    def test_force_reruns_everything(self, tmp_path):
        f = tmp_path / "c17.bench"
        f.write_text("# c17")
        output_dir = tmp_path / "out"
        calls = {"n": 0}

        def run_one(path):
            calls["n"] += 1
            return _FakeAnalysis(path.stem, coverage=100.0)

        run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)
        run_atpg_suite([f], run_one, output_dir, self._policy(), force=True, log=lambda *a: None)
        assert calls["n"] == 2

    def test_transient_failure_retries_within_one_run_then_succeeds(self, tmp_path):
        f = tmp_path / "flaky.bench"
        f.write_text("# flaky")
        output_dir = tmp_path / "out"
        calls = {"n": 0}

        def run_one(path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("Gemini failure: 503 UNAVAILABLE")
            return _FakeAnalysis(path.stem, coverage=100.0)

        counts = run_atpg_suite([f], run_one, output_dir, self._policy(), log=lambda *a: None)
        assert counts == {"done": 1, "failed": 0, "skipped": 0}
        assert calls["n"] == 3

    def test_transient_failure_exhausted_is_retryable_on_next_invocation(self, tmp_path):
        f = tmp_path / "always_flaky.bench"
        f.write_text("# always flaky")
        output_dir = tmp_path / "out"

        def always_fails(path):
            raise RuntimeError("rate limited")

        counts = run_atpg_suite([f], always_fails, output_dir, self._policy(), log=lambda *a: None)
        assert counts == {"done": 0, "failed": 1, "skipped": 0}

        # A fresh invocation (as a scheduled re-run would be) still retries it.
        calls = {"n": 0}

        def succeeds_this_time(path):
            calls["n"] += 1
            return _FakeAnalysis(path.stem, coverage=100.0)

        counts2 = run_atpg_suite([f], succeeds_this_time, output_dir, self._policy(), log=lambda *a: None)
        assert counts2 == {"done": 1, "failed": 0, "skipped": 0}
        assert calls["n"] == 1

    def test_permanent_failure_is_not_retried_on_next_invocation(self, tmp_path):
        f = tmp_path / "malformed.bench"
        f.write_text("# malformed")
        output_dir = tmp_path / "out"
        calls = {"n": 0}

        def always_malformed(path):
            calls["n"] += 1
            raise ValueError("combinational feedback loop")

        run_atpg_suite([f], always_malformed, output_dir, self._policy(), log=lambda *a: None)
        counts2 = run_atpg_suite([f], always_malformed, output_dir, self._policy(), log=lambda *a: None)

        assert calls["n"] == 1  # never called again on the second invocation
        assert counts2 == {"done": 0, "failed": 0, "skipped": 1}

    def test_fatal_failure_stops_the_whole_run_without_touching_later_circuits(self, tmp_path):
        from pyatpg.atpg.binary import AtalantaNotAvailable

        circuits = [tmp_path / f"{n}.bench" for n in ("first", "second", "third")]
        for f in circuits:
            f.write_text("# x")
        output_dir = tmp_path / "out"
        calls = []

        def run_one(path):
            calls.append(path.stem)
            raise AtalantaNotAvailable("binary missing")

        counts = run_atpg_suite(circuits, run_one, output_dir, self._policy(), log=lambda *a: None)
        assert calls == ["first"]  # stopped immediately, never tried second/third
        assert counts == {"done": 0, "failed": 0, "skipped": 0}

    def test_manifest_records_are_readable_after_a_run(self, tmp_path):
        f = tmp_path / "c17.bench"
        f.write_text("# c17")
        output_dir = tmp_path / "out"
        run_atpg_suite(
            [f], lambda path: _FakeAnalysis(path.stem, coverage=87.5),
            output_dir, self._policy(), log=lambda *a: None,
        )
        manifest = Manifest(output_dir / "manifest.json")
        record = manifest.get(str(f))
        assert record.status == "done"
        payload = json.loads((output_dir / "c17.json").read_text())
        assert payload["runs"]["non_targeted"]["results"]["fault_coverage"] == 87.5
