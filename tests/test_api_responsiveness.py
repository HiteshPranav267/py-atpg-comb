"""A long analysis must not block the server: /health and progress stay live."""

import threading
import time

from fastapi.testclient import TestClient

from pyatpg.api.app import app
from pyatpg.api.routes import atpg as route
from pyatpg.core import progress

COMB = "module t(a, b, y); input a, b; output y; and g1(y, a, b); endmodule\n"


def test_health_and_progress_answer_during_a_run(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    def slow_run(**kw):
        progress.report("Running Atalanta on the full fault list")
        started.set()
        release.wait(10)
        raise ValueError("done")  # mapped to 400; only the timing matters here

    monkeypatch.setattr(route._service, "run", slow_run)
    client = TestClient(app)
    result = {}

    def post():
        result["resp"] = client.post(
            "/api/atpg/run",
            files={"file": ("t.v", COMB, "text/plain")},
            data={"progress_id": "test-run-1"},
        )

    worker = threading.Thread(target=post)
    worker.start()
    try:
        assert started.wait(10), "analysis never started"
        t0 = time.monotonic()
        assert client.get("/health").status_code == 200
        snap = client.get("/api/atpg/progress/test-run-1").json()
        assert time.monotonic() - t0 < 5
        assert snap["stage"] == "Running Atalanta on the full fault list"
        assert snap["done"] is False
    finally:
        release.set()
        worker.join(10)
    assert result["resp"].status_code == 400
    assert client.get("/api/atpg/progress/test-run-1").json()["done"] is True


def test_unknown_progress_id_is_404():
    assert TestClient(app).get("/api/atpg/progress/nope").status_code == 404


def test_report_outside_a_tracked_request_is_a_no_op():
    progress.report("nobody is listening")  # must not raise


def test_cancelled_run_stops_at_its_next_checkpoint():
    import pytest

    from pyatpg.seq.engine import EngineConfig, SequentialAtpgEngine, _RunState

    with progress.track("cancel-me"):
        assert not progress.is_cancelled()
        progress.cancel("cancel-me")
        assert progress.is_cancelled()
        # The sequential engine stops searching...
        engine = SequentialAtpgEngine.__new__(SequentialAtpgEngine)
        engine.config = EngineConfig()
        assert engine._budget_left(_RunState(faults=[])) is False
        # ...and the next stage boundary raises.
        with pytest.raises(progress.RunCancelled):
            progress.report("next stage")
    assert not progress.is_cancelled()  # outside the run, nothing is cancelled
