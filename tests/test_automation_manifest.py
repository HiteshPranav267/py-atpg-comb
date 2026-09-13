from pyatpg.automation.manifest import Manifest, fingerprint_file


def test_fingerprint_changes_when_file_is_rewritten(tmp_path):
    f = tmp_path / "c17.bench"
    f.write_text("a")
    fp1 = fingerprint_file(f)
    f.write_text("a much longer different circuit body")
    fp2 = fingerprint_file(f)
    assert fp1 != fp2


class TestManifestLifecycle:
    def test_unknown_key_needs_run(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        assert m.needs_run("c17", "fp1") is True

    def test_done_with_matching_fingerprint_is_skipped(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_success("c17", "fp1", attempts=1, result={"circuit": "c17"})
        assert m.needs_run("c17", "fp1") is False

    def test_done_with_changed_fingerprint_needs_rerun(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_success("c17", "fp1", attempts=1, result={"circuit": "c17"})
        assert m.needs_run("c17", "fp2") is True

    def test_permanent_failure_is_not_auto_retried(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_failure("c17", "fp1", attempts=1, classification="permanent", error="bad netlist")
        assert m.needs_run("c17", "fp1") is False

    def test_transient_failure_is_retried(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_failure("c17", "fp1", attempts=6, classification="transient", error="503")
        assert m.needs_run("c17", "fp1") is True

    def test_force_overrides_everything(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_success("c17", "fp1", attempts=1, result={})
        assert m.needs_run("c17", "fp1", force=True) is True

    def test_running_left_by_a_crash_is_retried(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.start("c17", "fp1")
        assert m.needs_run("c17", "fp1") is True


class TestManifestPersistence:
    def test_survives_a_reload(self, tmp_path):
        path = tmp_path / "manifest.json"
        m1 = Manifest(path)
        m1.record_success("c17", "fp1", attempts=2, result={"circuit": "c17", "fault_coverage": 100.0})
        m1.record_failure("c432", "fp2", attempts=6, classification="transient", error="rate limited")

        m2 = Manifest(path)
        assert m2.needs_run("c17", "fp1") is False
        assert m2.needs_run("c432", "fp2") is True  # transient, retryable
        record = m2.get("c17")
        assert record.attempts == 2
        assert record.result["fault_coverage"] == 100.0

    def test_corrupt_manifest_does_not_crash_the_loader(self, tmp_path):
        path = tmp_path / "manifest.json"
        path.write_text("{not valid json")
        m = Manifest(path)  # must not raise
        assert m.needs_run("anything", "fp") is True

    def test_writes_are_atomic_no_stray_tmp_files(self, tmp_path):
        m = Manifest(tmp_path / "manifest.json")
        m.record_success("c17", "fp1", attempts=1, result={})
        leftovers = list(tmp_path.glob(".manifest-*.tmp"))
        assert leftovers == []


def test_credentials_failure_is_retried_like_transient(tmp_path):
    """Swapping a key or topping up billing should let the very next run
    succeed without needing --force -- same retry eligibility as a plain
    transient failure, just never retried within one invocation (that's
    retry.run_with_retries' job, not the manifest's)."""
    m = Manifest(tmp_path / "manifest.json")
    m.record_failure("c17", "fp1", attempts=1, classification="credentials", error="insufficient_quota")
    assert m.needs_run("c17", "fp1") is True
