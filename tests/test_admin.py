"""Admin endpoints: token protection, listing and clearing the run queue."""

import threading
import time

from fastapi.testclient import TestClient

from pyatpg.api.app import app
from pyatpg.api.routes import atpg as route
from pyatpg.core import progress

COMB = "module t(a, b, y); input a, b; output y; and g1(y, a, b); endmodule\n"
TOKEN = "s3cret-admin"


def test_admin_is_disabled_without_a_configured_token(monkeypatch):
    monkeypatch.delenv("PYATPG_ADMIN_TOKEN", raising=False)
    resp = TestClient(app).get("/api/admin/runs", headers={"X-Admin-Token": "anything"})
    assert resp.status_code == 503


def test_admin_rejects_a_wrong_token(monkeypatch):
    monkeypatch.setenv("PYATPG_ADMIN_TOKEN", TOKEN)
    client = TestClient(app)
    assert client.get("/api/admin/runs").status_code == 401
    assert client.get("/api/admin/runs", headers={"X-Admin-Token": "nope"}).status_code == 401
    assert client.post("/api/admin/runs/cancel", json={}).status_code == 401


def test_admin_lists_and_clears_running_and_queued_runs(monkeypatch):
    monkeypatch.setenv("PYATPG_ADMIN_TOKEN", TOKEN)
    started = threading.Event()

    def slow_run(**kw):
        progress.report("Pretending to run Atalanta")
        started.set()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            progress.check_cancelled()  # what every real stage boundary does
            time.sleep(0.05)
        raise AssertionError("never cancelled")

    monkeypatch.setattr(route._service, "run", slow_run)
    client = TestClient(app)
    headers = {"X-Admin-Token": TOKEN}
    results = {}

    def post(pid):
        results[pid] = client.post(
            "/api/atpg/run",
            files={"file": ("t.v", COMB, "text/plain")},
            data={"progress_id": pid},
        )

    first = threading.Thread(target=post, args=("admin-run-1",))
    first.start()
    assert started.wait(10)
    second = threading.Thread(target=post, args=("admin-run-2",))
    second.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        listing = client.get("/api/admin/runs", headers=headers).json()
        if listing["running"] and listing["queued"]:
            break
        time.sleep(0.1)
    assert [r["id"] for r in listing["running"]] == ["admin-run-1"]
    assert [r["id"] for r in listing["queued"]] == ["admin-run-2"]
    assert listing["running"][0]["stage"] == "Pretending to run Atalanta"

    resp = client.post("/api/admin/runs/cancel", json={}, headers=headers)
    assert resp.json() == {"cancelled": 2}
    first.join(15)
    second.join(15)
    assert results["admin-run-1"].status_code == 499
    assert results["admin-run-2"].status_code == 499
    after = client.get("/api/admin/runs", headers=headers).json()
    assert after["running"] == [] and after["queued"] == []


def test_cancelling_an_unknown_run_is_404(monkeypatch):
    monkeypatch.setenv("PYATPG_ADMIN_TOKEN", TOKEN)
    resp = TestClient(app).post("/api/admin/runs/cancel", json={"id": "missing"},
                                headers={"X-Admin-Token": TOKEN})
    assert resp.status_code == 404


def _admin(monkeypatch):
    monkeypatch.setenv("PYATPG_ADMIN_TOKEN", TOKEN)
    return TestClient(app), {"X-Admin-Token": TOKEN}


def test_status_reports_server_and_tools(monkeypatch):
    client, headers = _admin(monkeypatch)
    body = client.get("/api/admin/status", headers=headers).json()
    for key in ("uptime_s", "memory", "python", "tools", "slots", "slots_in_use", "maintenance"):
        assert key in body
    assert set(body["tools"]) == {"atalanta", "yosys", "ghdl"}


def test_settings_can_be_changed_live_and_reset(monkeypatch):
    from pyatpg.api.concurrency import slot_count
    from pyatpg.ranking.providers import request_timeout_s

    client, headers = _admin(monkeypatch)
    try:
        resp = client.put("/api/admin/settings", json={"key": "llm_timeout_s", "value": 12},
                          headers=headers)
        assert resp.status_code == 200
        assert request_timeout_s() == 12
        row = next(r for r in resp.json()["settings"] if r["key"] == "llm_timeout_s")
        assert row["source"] == "admin override"
        client.put("/api/admin/settings", json={"key": "max_concurrent_runs", "value": 3},
                   headers=headers)
        assert slot_count() == 3
        bad = client.put("/api/admin/settings", json={"key": "max_concurrent_runs", "value": 0},
                         headers=headers)
        assert bad.status_code == 400
        assert client.put("/api/admin/settings", json={"key": "nope", "value": 1},
                          headers=headers).status_code == 404
    finally:
        for key in ("llm_timeout_s", "max_concurrent_runs"):
            client.put("/api/admin/settings", json={"key": key, "value": None}, headers=headers)
    assert slot_count() == 1


def test_maintenance_mode_rejects_new_runs_and_is_logged(monkeypatch):
    client, headers = _admin(monkeypatch)
    try:
        client.put("/api/admin/settings", json={"key": "maintenance", "value": True}, headers=headers)
        resp = client.post("/api/atpg/run", files={"file": ("t.v", COMB, "text/plain")})
        assert resp.status_code == 503 and "maintenance" in resp.json()["detail"]
        parse = client.post("/api/circuit/parse", files={"file": ("t.v", COMB, "text/plain")})
        assert parse.status_code == 503
    finally:
        client.put("/api/admin/settings", json={"key": "maintenance", "value": None}, headers=headers)
    runs = client.get("/api/admin/history", headers=headers).json()["runs"]
    assert runs[0]["outcome"] == "rejected"
    ok = client.post("/api/circuit/parse", files={"file": ("t.v", COMB, "text/plain")})
    assert ok.status_code == 200
    assert client.get("/api/admin/history", headers=headers).json()["runs"][0]["outcome"] == "ok"


def test_storage_lists_and_clears_caches(monkeypatch, tmp_path):
    from pyatpg.atpg import llm_context
    from pyatpg.llm import circuit_context

    comb_cache, seq_cache = tmp_path / "comb.json", tmp_path / "seq.json"
    comb_cache.write_text("x" * 2048)
    seq_cache.write_text("y" * 1024)
    monkeypatch.setattr(llm_context, "DEFAULT_CACHE_PATH", str(comb_cache))
    monkeypatch.setattr(circuit_context, "_DEFAULT_CACHE_PATH", str(seq_cache))
    client, headers = _admin(monkeypatch)
    rows = {r["key"]: r for r in client.get("/api/admin/storage", headers=headers).json()["storage"]}
    assert set(rows) == {"synthesis_cache", "llm_cache", "job_workspaces"}
    assert rows["llm_cache"]["size_bytes"] == 3072
    resp = client.post("/api/admin/storage/clear", json={"target": "llm_cache"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["freed_bytes"] == 3072
    assert not comb_cache.exists() and not seq_cache.exists()
    assert client.post("/api/admin/storage/clear", json={"target": "nope"},
                       headers=headers).status_code == 404


def test_restart_schedules_a_process_exit(monkeypatch):
    from pyatpg.api.routes import admin as admin_route

    scheduled = {}

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            scheduled.update(delay=delay, fn=fn, args=args)

        def start(self):
            scheduled["started"] = True

    monkeypatch.setattr(admin_route.threading, "Timer", FakeTimer)
    client, headers = _admin(monkeypatch)
    assert client.post("/api/admin/restart", headers=headers).json()["restarting"] is True
    assert scheduled["started"] and scheduled["args"] == (0,)
