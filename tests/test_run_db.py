"""Run database recorder: classification and result -> row mapping (no database needed)."""

from pyatpg.core import run_db


def test_recording_is_off_under_tests():
    assert run_db.enabled() is False


def test_classifies_benchmark_suites():
    assert run_db.classify("c432.bench")["source_suite"] == "iscas85"
    s = run_db.classify("s38584_scan.v")
    assert (s["source_suite"], s["benchmark_family"], s["has_scan"]) == ("iscas89", "s38584", True)
    assert run_db.classify("b12.v")["source_suite"] == "itc99"
    aes = run_db.classify("AES-T900.zip.v")
    assert (aes["source_suite"], aes["trojan_id"], aes["trojan_label"]) == (
        "trust_hub_chip", "AES-T900", "trojan_inserted")
    assert run_db.classify("AES-T900_TjFree.v")["trojan_label"] == "trojan_free"
    gen = run_db.classify("c432_variant_003.v")
    assert (gen["source_suite"], gen["benchmark_family"]) == ("generated_trojan", "c432")
    assert run_db.classify("my_design.v")["source_suite"] == "custom"


def test_error_classification():
    from pyatpg.core import progress
    from pyatpg.core.rtl import DesignTooLargeError

    assert run_db.classify_error(progress.RunCancelled("x"))[:2] == ("cancelled", 499)
    assert run_db.classify_error(DesignTooLargeError("big"))[:2] == ("rejected", 400)
    assert run_db.classify_error(RuntimeError("Atalanta timed out after 600s"))[0] == "timeout"


def test_scrubs_keys_from_error_text():
    assert "gsk_" not in run_db.scrub("bad key gsk_abcdefghijklmnop rejected")


def test_describes_a_combinational_compare_run():
    payload = {
        "job_id": "j1", "mode": "compare",
        "circuit": {"name": "c17", "primary_inputs": 5, "primary_outputs": 2, "gates": 6,
                    "flip_flops": 0, "logic_depth": 3},
        "runs": {
            "non_targeted": {"results": {"fault_coverage": 100.0, "collapsed_faults": 22,
                                         "patterns_after_compaction": 7},
                             "parameters": {}, "timing": {"total": 0.01}, "command": ["atalanta"],
                             "faults": [{"gate": "g1", "fault_type": "/1", "status": "redundant"}],
                             "artifacts": {"test_vectors": "c17.test"}},
            "targeted": {"results": {"fault_coverage": 100.0}, "parameters": {}, "timing": {},
                         "command": ["atalanta", "-b", "50", "-f", "x"], "targeted_gates": ["g1", "g2"]},
        },
        "comparison": {"targeted_coverage": 100.0, "non_targeted_coverage": 100.0,
                       "simulated_coverage_delta": 0.0},
        "targeting": {"strategy": "llm", "used_llm": True, "gates": ["g1", "g2"]},
        "verdict": {"verdict": "no_trojan_indicated", "confidence": 0.1, "suspects": []},
        "llm_usage": {"enabled": True, "provider": "Groq", "model": "openai/gpt-oss-120b",
                      "api_calls": 1, "total_tokens_est": 900,
                      "by_stage": {"gate_targeting": {"calls": 1, "tokens_est": 900}}},
        "notes": [],
    }
    d = run_db.describe_atpg(payload)
    assert d["circuit"]["circuit_kind"] == "combinational"
    assert d["run"]["fault_coverage"] == 100.0 and d["run"]["patterns"] == 7
    targeted = next(r for r in d["comb_runs"] if r["kind"] == "targeted")
    assert targeted["backtrack_limit"] == 50 and targeted["targeted_fault_count"] == 4
    assert d["comb_faults"]["non_targeted"] == [{"gate": "g1", "stuck_at": 1, "status": "redundant"}]
    assert d["comb_comparison"]["sim_coverage_delta"] == 0.0
    assert d["gate_selection"]["gate_count"] == 2
    assert d["llm_usage"][0]["stage"] == "gate_targeting"
    assert d["run"]["trojan_verdict"] == "no_trojan_indicated"


def test_describes_a_sequential_comparison():
    analysis = lambda cov: {"results": {"fault_coverage": cov, "sequence_count": 3},  # noqa: E731
                            "parameters": {"time_budget_s": 40}, "timing": {"total_s": 2.0},
                            "fault_details": [{"node": "n1", "stuck_at": 0, "status": "podem_exhausted"}]}
    payload = {
        "mode": "compare", "circuit": {"name": "s27", "flip_flops": 3},
        "sequential": {"comparison": {"baseline": analysis(80.0), "llm_assisted": analysis(85.0),
                                      "coverage_delta": 5.0, "additional_faults_detected": ["a", "b"]},
                       "analysis": analysis(85.0), "llm_usage": {"api_calls": 2}},
    }
    d = run_db.describe_atpg(payload)
    assert d["circuit"]["circuit_kind"] == "sequential"
    assert {r["kind"] for r in d["seq_runs"]} == {"deterministic", "llm_assisted"}
    assert d["seq_comparison"]["additional_faults_detected"] == 2
    assert d["run"]["fault_coverage"] == 85.0
    assert d["seq_faults"]["deterministic"][0]["status"] == "podem_exhausted"


def test_admin_db_endpoints_say_when_recording_is_off(monkeypatch):
    from fastapi.testclient import TestClient

    from pyatpg.api.app import app

    monkeypatch.setenv("PYATPG_ADMIN_TOKEN", "t")
    client = TestClient(app)
    status = client.get("/api/admin/db/status", headers={"X-Admin-Token": "t"}).json()
    assert status["enabled"] is False
    assert client.get("/api/admin/db/runs", headers={"X-Admin-Token": "t"}).status_code == 503
    assert client.get("/api/admin/db/runs").status_code == 401
