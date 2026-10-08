"""Admin endpoints: see what is occupying the server and clear the queue.

Disabled unless the server has an admin token configured
(``PYATPG_ADMIN_TOKEN``); every request must then send it in the
``X-Admin-Token`` header. Without that, anyone who can reach the public API
could cancel other people's analyses.
"""
from __future__ import annotations

import hmac
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from pyatpg.api import concurrency
from pyatpg.api.concurrency import slot_count
from pyatpg.core import progress, settings

#: When this process started, for the uptime shown on the admin page.
_STARTED = time.time()

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = os.environ.get("PYATPG_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin features are disabled on this server: set the PYATPG_ADMIN_TOKEN "
                   "environment variable to enable them.",
        )
    if not hmac.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


@router.get("/runs", dependencies=[Depends(require_admin)])
def list_runs() -> Dict[str, Any]:
    """Every analysis that is running or waiting for a slot."""
    runs = progress.list_runs()
    return {
        "slots": slot_count(),
        "running": [r for r in runs if r["state"] == "running"],
        "queued": [r for r in runs if r["state"] == "queued"],
    }


class CancelRequest(BaseModel):
    #: One run to cancel; omitted = cancel every queued and running run.
    id: Optional[str] = None


@router.post("/runs/cancel", dependencies=[Depends(require_admin)])
def cancel_runs(req: CancelRequest) -> Dict[str, Any]:
    """Cancel one run, or clear the whole queue (running runs included).

    Cancelled runs stop at their next checkpoint -- normally within a second
    or two -- and free their slot.
    """
    if req.id:
        if not progress.cancel(req.id):
            raise HTTPException(status_code=404, detail="No such run.")
        return {"cancelled": 1}
    return {"cancelled": progress.cancel_all()}


# ----------------------------------------------------------------------
# Server status
# ----------------------------------------------------------------------

def _read_first(path: str, prefix: str = "") -> str | None:
    try:
        with open(path, encoding="ascii") as h:
            for line in h:
                if line.startswith(prefix):
                    return line[len(prefix):].strip()
    except OSError:
        return None
    return None


def _memory() -> Dict[str, Any]:
    """This process's resident memory and the container's limit, in MB, where readable."""
    rss = None
    raw = _read_first("/proc/self/status", "VmRSS:")
    if raw:
        rss = int(raw.split()[0]) // 1024
    else:
        try:
            import psutil

            rss = psutil.Process().memory_info().rss // 2**20
        except Exception:  # noqa: BLE001 - optional dependency / unsupported platform
            rss = None
    limit = None
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read_first(path)
        if raw and raw.isdigit() and int(raw) < 2**50:
            limit = int(raw) // 2**20
            break
    return {"rss_mb": rss, "limit_mb": limit}


def _commit() -> str | None:
    commit = os.environ.get("RENDER_GIT_COMMIT")
    if commit:
        return commit[:12]
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True,
                             text=True, timeout=5, cwd=Path(__file__).resolve().parents[3])
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - not a git checkout
        return None


def _tools() -> Dict[str, str]:
    tools: Dict[str, str] = {}
    try:
        from pyatpg.atpg.binary import resolve_atalanta_binary

        tools["atalanta"] = str(resolve_atalanta_binary().path)
    except Exception as exc:  # noqa: BLE001 - report, don't fail
        tools["atalanta"] = f"unavailable: {exc}"
    native = shutil.which("yosys")
    if native:
        tools["yosys"] = native
    else:
        try:
            import yowasp_yosys  # noqa: F401

            tools["yosys"] = "yowasp-yosys (pip, WebAssembly)"
        except ImportError:
            tools["yosys"] = "unavailable (RTL uploads will fail)"
    tools["ghdl"] = shutil.which("ghdl") or "unavailable (VHDL uploads will fail)"
    return tools


@router.get("/status", dependencies=[Depends(require_admin)])
def status() -> Dict[str, Any]:
    return {
        "uptime_s": round(time.time() - _STARTED),
        "memory": _memory(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "platform": platform.platform(terse=True),
        "hosted": bool(os.environ.get("RENDER")),
        "commit": _commit(),
        "tools": _tools(),
        "slots": slot_count(),
        "slots_in_use": concurrency.slots_in_use(),
        "maintenance": bool(settings.get("maintenance")),
    }


# ----------------------------------------------------------------------
# Live settings
# ----------------------------------------------------------------------

@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings() -> Dict[str, Any]:
    return {"settings": settings.describe()}


class SettingUpdate(BaseModel):
    key: str
    #: New value; null removes the admin override (back to env/default).
    value: Optional[Any] = None


@router.put("/settings", dependencies=[Depends(require_admin)])
def update_setting(req: SettingUpdate) -> Dict[str, Any]:
    """Change a setting on the running server (until the next restart)."""
    if req.key not in settings.SETTINGS:
        raise HTTPException(status_code=404, detail=f"Unknown setting {req.key!r}.")
    try:
        settings.set_override(req.key, req.value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.key == "max_concurrent_runs":
        concurrency.slots_changed()
    return {"settings": settings.describe()}


# ----------------------------------------------------------------------
# Recent runs
# ----------------------------------------------------------------------

@router.get("/history", dependencies=[Depends(require_admin)])
def history() -> Dict[str, Any]:
    """The last 50 finished requests, newest first."""
    return {"runs": concurrency.recent_runs()}


@router.delete("/history", dependencies=[Depends(require_admin)])
def clear_history() -> Dict[str, Any]:
    concurrency.clear_history()
    return {"runs": []}


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------

def _storage_targets() -> Dict[str, Dict[str, Any]]:
    from pyatpg.atpg import llm_context
    from pyatpg.atpg.workspace import jobs_root
    from pyatpg.llm import circuit_context

    return {
        "synthesis_cache": {
            "label": "RTL synthesis cache",
            "help": "Yosys netlists of uploaded RTL; re-uploading the same design re-synthesizes.",
            "paths": [Path(tempfile.gettempdir()) / "pyatpg_rtl_cache"],
        },
        "llm_cache": {
            "label": "LLM answer caches",
            "help": "Reused model answers; clearing makes the next runs call the LLM again.",
            "paths": [Path(llm_context.DEFAULT_CACHE_PATH), Path(circuit_context._DEFAULT_CACHE_PATH)],
        },
        "job_workspaces": {
            "label": "ATPG job workspaces",
            "help": "Atalanta inputs/outputs of past runs (artifact downloads). Old ones are "
                    "pruned automatically.",
            "paths": [jobs_root()],
        },
    }


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    if path.is_dir():
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    return total


@router.get("/storage", dependencies=[Depends(require_admin)])
def storage() -> Dict[str, Any]:
    rows = []
    for key, t in _storage_targets().items():
        size = sum(_size(p) for p in t["paths"])
        rows.append({"key": key, "label": t["label"], "help": t["help"], "size_bytes": size})
    return {"storage": rows}


class ClearRequest(BaseModel):
    target: str


@router.post("/storage/clear", dependencies=[Depends(require_admin)])
def clear_storage(req: ClearRequest) -> Dict[str, Any]:
    targets = _storage_targets()
    if req.target not in targets:
        raise HTTPException(status_code=404, detail=f"Unknown storage target {req.target!r}.")
    freed = 0
    for path in targets[req.target]["paths"]:
        freed += _size(path)
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    return {"freed_bytes": freed, **storage()}


# ----------------------------------------------------------------------
# Restart
# ----------------------------------------------------------------------

@router.post("/restart", dependencies=[Depends(require_admin)])
def restart() -> Dict[str, Any]:
    """Exit the server process shortly after answering.

    On Render (and any supervisor that restarts a stopped service) the
    backend comes back on its own within about a minute; a server started by
    hand stays stopped. In-flight analyses are lost.
    """
    threading.Timer(1.0, os._exit, args=(0,)).start()
    return {"restarting": True, "hosted": bool(os.environ.get("RENDER"))}


# ----------------------------------------------------------------------
# Run database (PostgreSQL, see pyatpg.core.run_db)
# ----------------------------------------------------------------------

_DB_COLUMNS = (
    "id", "received_at", "run_type", "outcome", "http_status", "duration_s", "queue_wait_s",
    "circuit", "source_suite", "benchmark_family", "trojan_label", "circuit_kind", "design_level",
    "gates", "flip_flops", "mode", "use_llm", "llm_provider", "llm_model", "fault_coverage",
    "patterns", "llm_api_calls", "llm_total_tokens", "trojan_verdict", "hosted",
    "upload_filename", "error_class", "error_message",
)
_DB_FILTERS = ("run_type", "outcome", "source_suite", "circuit_kind", "llm_provider", "trojan_label")


def _require_db() -> None:
    from pyatpg.core import run_db

    if not run_db.enabled():
        raise HTTPException(status_code=503, detail="The run database is not configured: set "
                                                    "DATABASE_URL on the server.")


def _query_runs(filters: Dict[str, Optional[str]], search: Optional[str], limit: int,
                offset: int) -> tuple[list[dict[str, Any]], int]:
    from psycopg import sql
    from psycopg.rows import dict_row

    from pyatpg.core import run_db

    where, params = [], []
    for key in _DB_FILTERS:
        if filters.get(key):
            where.append(sql.SQL("o.{}::text = %s").format(sql.Identifier(key)))
            params.append(filters[key])
    if search:
        where.append(sql.SQL("(o.circuit ILIKE %s OR r.upload_filename ILIKE %s)"))
        params += [f"%{search}%"] * 2
    clause = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL("")
    base = sql.SQL(
        "FROM v_run_overview o JOIN runs r USING (id)"
    )
    cols = sql.SQL(", ").join(
        sql.SQL("r.{}").format(sql.Identifier(c)) if c in ("http_status", "upload_filename",
                                                           "error_class", "error_message")
        else sql.SQL("o.{}").format(sql.Identifier(c))
        for c in _DB_COLUMNS
    )
    with run_db.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql.SQL("SELECT count(*) AS n ") + base + clause, params)
        total = cur.fetchone()["n"]
        cur.execute(sql.SQL("SELECT ") + cols + sql.SQL(" ") + base + clause
                    + sql.SQL(" ORDER BY o.received_at DESC LIMIT %s OFFSET %s"),
                    params + [limit, offset])
        rows = cur.fetchall()
    for row in rows:
        row["id"] = str(row["id"])
        row["received_at"] = row["received_at"].isoformat() if row["received_at"] else None
    return rows, total


@router.get("/db/status", dependencies=[Depends(require_admin)])
def db_status() -> Dict[str, Any]:
    """Whether runs are being recorded, and how many there are."""
    from pyatpg.core import run_db

    out: Dict[str, Any] = run_db.status()
    if not out["enabled"]:
        return out
    try:
        with run_db.connect() as conn:
            out["total_runs"] = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
            out["runs_last_24h"] = conn.execute(
                "SELECT count(*) FROM runs WHERE received_at > now() - interval '1 day'").fetchone()[0]
            out["circuits"] = conn.execute("SELECT count(*) FROM circuits").fetchone()[0]
            out["by_outcome"] = dict(conn.execute(
                "SELECT outcome::text, count(*) FROM runs GROUP BY 1").fetchall())
    except Exception as exc:  # noqa: BLE001 - show the problem on the admin page
        out["connection_error"] = run_db.scrub(str(exc))[:500]
    return out


@router.get("/db/runs", dependencies=[Depends(require_admin)])
def db_runs(limit: int = 50, offset: int = 0, search: Optional[str] = None,
            run_type: Optional[str] = None, outcome: Optional[str] = None,
            source_suite: Optional[str] = None, circuit_kind: Optional[str] = None,
            llm_provider: Optional[str] = None, trojan_label: Optional[str] = None) -> Dict[str, Any]:
    """Recorded runs, newest first, with optional filters."""
    _require_db()
    filters = dict(run_type=run_type, outcome=outcome, source_suite=source_suite,
                   circuit_kind=circuit_kind, llm_provider=llm_provider, trojan_label=trojan_label)
    rows, total = _query_runs(filters, search, max(1, min(limit, 500)), max(0, offset))
    return {"runs": rows, "total": total}


@router.get("/db/runs.csv", dependencies=[Depends(require_admin)])
def db_runs_csv(search: Optional[str] = None, run_type: Optional[str] = None,
                outcome: Optional[str] = None, source_suite: Optional[str] = None,
                circuit_kind: Optional[str] = None, llm_provider: Optional[str] = None,
                trojan_label: Optional[str] = None):
    """The same rows as CSV (up to 50,000), for spreadsheets and analysis."""
    import csv
    import io

    from fastapi.responses import Response

    _require_db()
    filters = dict(run_type=run_type, outcome=outcome, source_suite=source_suite,
                   circuit_kind=circuit_kind, llm_provider=llm_provider, trojan_label=trojan_label)
    rows, _ = _query_runs(filters, search, 50000, 0)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_DB_COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="pyatpg-runs.csv"'})


@router.get("/db/summary", dependencies=[Depends(require_admin)])
def db_summary() -> Dict[str, Any]:
    """Runs and mean coverage per benchmark suite and per LLM, successful ATPG runs only."""
    from psycopg.rows import dict_row

    from pyatpg.core import run_db

    _require_db()
    with run_db.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT source_suite::text AS suite, circuit_kind::text AS kind, count(*) AS runs,
                   round(avg(fault_coverage)::numeric, 2)::float AS mean_coverage,
                   round(avg(duration_s)::numeric, 1)::float AS mean_duration_s
            FROM v_run_overview WHERE run_type = 'atpg' AND outcome = 'ok'
            GROUP BY 1, 2 ORDER BY runs DESC""")
        by_suite = cur.fetchall()
        cur.execute("""
            SELECT coalesce(llm_provider, 'none') AS provider, coalesce(llm_model, '') AS model,
                   count(*) AS runs,
                   round(avg(coverage_delta)::numeric, 3)::float AS mean_coverage_delta
            FROM v_llm_effect GROUP BY 1, 2 ORDER BY runs DESC""")
        by_llm = cur.fetchall()
    return {"by_suite": by_suite, "by_llm": by_llm}
