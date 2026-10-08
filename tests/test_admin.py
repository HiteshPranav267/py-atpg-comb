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
