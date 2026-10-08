"""Record every run in the PostgreSQL run database (see db/schema.sql).

Enabled when ``DATABASE_URL`` is set and ``psycopg`` is installed; otherwise
every function here is a no-op. Recording never slows down or breaks a
request: rows are written by one background thread, failures are kept for
the admin page (:func:`status`) and otherwise ignored, and a full queue drops
the record rather than block.

What is stored is classified as described in docs/run-database.md. API keys
never reach this module, and error text is scrubbed of anything key-shaped
before it is stored.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DATABASE_URL_ENV_VAR = "DATABASE_URL"
#: Set to 0/false/off to turn recording off even with DATABASE_URL set (tests do).
RECORD_ENV_VAR = "PYATPG_RECORD_RUNS"

#: Per engine run; Atalanta's -l log lists only redundant/aborted faults and
#: the sequential engine only undetected ones, but an AES-sized design can
#: still produce tens of thousands.
MAX_FAULT_ROWS = 20000

_ERROR_TEXT_LIMIT = 2000
_KEY_PATTERN = re.compile(r"(sk-ant-|sk-|gsk_|AIza|nvapi-|xai-)[A-Za-z0-9_\-]{8,}")


# ----------------------------------------------------------------------
# Configuration / status
# ----------------------------------------------------------------------

def database_url() -> str | None:
    return (os.environ.get(DATABASE_URL_ENV_VAR) or "").strip() or None


def enabled() -> bool:
    if (os.environ.get(RECORD_ENV_VAR) or "1").strip().lower() in ("0", "false", "off", "no"):
        return False
    if not database_url():
        return False
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


_stats_lock = threading.Lock()
_stats: dict[str, Any] = {"written": 0, "failed": 0, "dropped": 0, "last_error": None,
                          "last_error_at": None, "last_written_at": None}


def status() -> dict[str, Any]:
    """Recorder state for the admin page."""
    url = database_url()
    with _stats_lock:
        out = dict(_stats)
    out.update(
        enabled=enabled(),
        configured=bool(url),
        host=urlparse(url).hostname if url else None,
        queued=_queue.qsize(),
    )
    return out


def _note(key: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _stats_lock:
        _stats[key] += 1
        if error is not None:
            _stats["last_error"] = scrub(error)[:500]
            _stats["last_error_at"] = now
        elif key == "written":
            _stats["last_written_at"] = now


def scrub(text: str | None) -> str:
    return _KEY_PATTERN.sub("[redacted]", text or "")


# ----------------------------------------------------------------------
# Connection (used by the writer thread and the admin read endpoints)
# ----------------------------------------------------------------------

def connect():
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=15, autocommit=False)


# ----------------------------------------------------------------------
# Background writer
# ----------------------------------------------------------------------

_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=200)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def submit(record: dict[str, Any]) -> None:
    """Queue one run for writing. Never raises."""
    if not enabled():
        return
    try:
        _ensure_worker()
        _queue.put_nowait(record)
    except queue.Full:
        _note("dropped", "Write queue full; a run was not recorded.")
    except Exception as exc:  # noqa: BLE001
        _note("failed", f"{type(exc).__name__}: {exc}")


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="run-db-writer", daemon=True)
            _worker.start()


def _work() -> None:
    conn = None
    while True:
        record = _queue.get()
        try:
            details = record.pop("describe", None)
            result = record.pop("result", None)
            if details is not None and result is not None:
                try:
                    record["details"] = details(result) or {}
                except Exception as exc:  # noqa: BLE001 - still record the run itself
                    record.setdefault("details", {})
                    log.warning("run_db: describing the result failed: %s", exc)
            for attempt in (1, 2):
                try:
                    if conn is None or conn.closed:
                        conn = connect()
                    write(conn, record)
                    _note("written")
                    break
                except Exception as exc:  # noqa: BLE001 - reconnect and retry once
                    try:
                        if conn is not None:
                            conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
                    if attempt == 2:
                        _note("failed", f"{type(exc).__name__}: {exc}")
                        log.warning("run_db: could not record run: %s", scrub(str(exc)))
        finally:
            _queue.task_done()


def flush(timeout: float = 30.0) -> bool:
    """Wait for queued writes (tests and shutdown). True when the queue drained."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _queue.unfinished_tasks


# ----------------------------------------------------------------------
# Building a record
# ----------------------------------------------------------------------

_commit_cache: list[str | None] = []


def backend_commit() -> str | None:
    if not _commit_cache:
        commit = os.environ.get("RENDER_GIT_COMMIT")
        if not commit:
            try:
                out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], capture_output=True,
                                     text=True, timeout=5, cwd=Path(__file__).resolve().parents[2])
                commit = out.stdout.strip() or None
            except Exception:  # noqa: BLE001
                commit = None
        _commit_cache.append(commit[:12] if commit else None)
    return _commit_cache[0]


def _rss_mb() -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as h:
            for line in h:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    try:
        import psutil

        return psutil.Process().memory_info().rss // 2**20
    except Exception:  # noqa: BLE001
        return None


def client_hash(request) -> str | None:
    """Salted hash of the client address: counts distinct users, identifies no one."""
    try:
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "")
    except Exception:  # noqa: BLE001
        return None
    if not ip:
        return None
    salt = os.environ.get("PYATPG_CLIENT_SALT") or database_url() or ""
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:16]


def upload_info(text: str, filename: str, raw: bytes | None = None) -> dict[str, Any]:
    """Hash, size and classification of an uploaded design (before any parsing)."""
    from pyatpg.core import rtl

    data = raw if raw is not None else text.encode("utf-8", "replace")
    lower = filename.lower()
    bundle = rtl.is_bundle(text)
    file_count = 1
    if bundle:
        try:
            file_count = len(rtl.unpack_sources(text)[0]) or 1
        except Exception:  # noqa: BLE001
            pass
    if lower.endswith(".bench"):
        fmt = "bench"
    elif bundle:
        fmt = "bundle"
    elif lower.endswith((".vhd", ".vhdl")):
        fmt = "vhdl"
    else:
        try:
            fmt = "verilog_rtl" if rtl.needs_synthesis(text) else "verilog_gate"
        except Exception:  # noqa: BLE001
            fmt = "verilog_gate"
    level = "gate" if fmt in ("bench", "verilog_gate") else "rtl"
    if fmt == "bundle":
        try:
            level = "rtl" if rtl.needs_synthesis(text) else "gate"
        except Exception:  # noqa: BLE001
            pass
    info = {
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "upload_filename": filename,
        "upload_bytes": len(data),
        "file_count": file_count,
        "input_format": fmt,
        "design_level": level,
        "synthesized": level == "rtl",
        "circuit_kind": _guess_kind(text, fmt),
    }
    info.update(classify(filename, text[:20000]))
    return info


_SEQ_HINT = re.compile(r"\bDFF|always\s*@\s*\(\s*(posedge|negedge)|\bdff\b|_reg\b|rising_edge", re.I)


def _guess_kind(text: str, fmt: str) -> str:
    return "sequential" if _SEQ_HINT.search(text[:500000]) else "combinational"


_TRUST_HUB = re.compile(r"([A-Za-z][A-Za-z0-9]*?)[-_]T(\d{3,4})(?![0-9])")


def classify(filename: str, text_head: str = "") -> dict[str, Any]:
    """Benchmark suite, family and Trojan label guessed from names.

    c432 -> ISCAS-85, s38584(_scan) -> ISCAS-89, b12 -> ITC-99, AES-T900 ->
    Trust-Hub chip-level (Trojan inserted unless a TjFree copy was chosen),
    the tool's own generated variants -> generated_trojan.
    """
    stem = Path(filename.replace("\\", "/")).name
    base = stem.rsplit(".", 1)[0]
    lower = base.lower()
    head = text_head.lower()
    out: dict[str, Any] = {"source_suite": "custom", "benchmark_family": None,
                           "trojan_label": "unknown", "trojan_id": None,
                           "has_scan": "scan" in lower, "name_hint": base}
    trust = _TRUST_HUB.search(base) or _TRUST_HUB.search(text_head[:4000])
    if trust:
        family = trust.group(1).upper()
        out.update(source_suite="trust_hub_chip", benchmark_family=family,
                   trojan_id=f"{family}-T{trust.group(2)}", trojan_label="trojan_inserted")
        if "tjfree" in lower or ("tjfree" in head and "tjin" not in head):
            out["trojan_label"] = "trojan_free"
        return out
    if "tjfree" in lower or "tjfree" in head:
        out.update(source_suite="trust_hub_chip", trojan_label="trojan_free")
        return out
    if re.search(r"(variant|_trojan|trojan_|_tj\d|_infected)", lower):
        out.update(source_suite="generated_trojan", trojan_label="trojan_inserted",
                   trojan_id=base)
        family = re.match(r"([cbs]\d+)", lower)
        out["benchmark_family"] = family.group(1) if family else None
        return out
    m = re.match(r"(c\d+)(?:_|$)", lower)
    if m:
        out.update(source_suite="iscas85", benchmark_family=m.group(1), trojan_label="trojan_free")
        return out
    m = re.match(r"(s\d+)(?:_|$)", lower)
    if m:
        out.update(source_suite="iscas89", benchmark_family=m.group(1), trojan_label="trojan_free")
        return out
    m = re.match(r"(b\d\d)(?:_|$)", lower)
    if m:
        out.update(source_suite="itc99", benchmark_family=m.group(1), trojan_label="trojan_free")
    return out


def llm_info(*, use_llm: bool, api_key: str | None, provider: str | None, model: str | None,
             base_url: str | None, fallbacks: list | None = None) -> dict[str, Any]:
    """Resolved provider/model for a request -- never the key."""
    if not use_llm:
        return {"use_llm": False, "llm_key_source": "none"}
    from pyatpg.ranking.providers import resolve_llm_config

    try:
        config = resolve_llm_config(provider=provider, api_key=api_key, model=model,
                                    base_url=base_url, require_model=False)
    except ValueError:
        config = None
    if config is None:
        return {"use_llm": True, "llm_key_source": "none", "llm_provider": provider or None,
                "llm_model": model or None}
    return {
        "use_llm": True,
        "llm_key_source": "request" if api_key else "server_env",
        "llm_provider": config.provider,
        "llm_model": config.model or None,
        "llm_base_url_host": urlparse(config.base_url).hostname if config.base_url else None,
        "llm_fallback_keys": len(fallbacks or []),
    }


def classify_error(exc: BaseException) -> tuple[str, int, str]:
    """(outcome, http_status, error_class) for an exception a run ended with."""
    from fastapi import HTTPException

    from pyatpg.core import progress

    name = type(exc).__name__
    text = str(exc).lower()
    if isinstance(exc, progress.RunCancelled):
        return "cancelled", 499, name
    if isinstance(exc, HTTPException):
        outcome = "rejected" if exc.status_code == 503 and "maintenance" in str(exc.detail) else "error"
        return outcome, exc.status_code, name
    if name == "DesignTooLargeError":
        return "rejected", 400, name
    if "timed out" in text or "timeout" in name.lower():
        return "timeout", 502, name
    if name in ("AtalantaNotAvailable",):
        return "error", 503, name
    if name in ("AtalantaExecutionError", "LLMUnavailableError"):
        return "error", 502, name
    if isinstance(exc, ValueError):
        return "error", 400, name
    return "error", 500, name


def settings_snapshot() -> dict[str, Any]:
    from pyatpg.core import settings

    out = {}
    for key in settings.SETTINGS:
        try:
            out[key] = settings.get(key)
        except Exception:  # noqa: BLE001
            pass
    return out


def base_record(*, run_type: str, endpoint: str, label: str, progress_id: str | None,
                request=None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "run_type": run_type,
        "endpoint": endpoint,
        "label": label,
        "progress_id": progress_id,
        "received_at": datetime.now(timezone.utc),
        "client_hash": client_hash(request) if request is not None else None,
        "meta": dict(meta or {}),
    }


def finish_record(record: dict[str, Any], *, started_at: datetime | None,
                  outcome: str, http_status: int, error: BaseException | None = None,
                  error_stage: str | None = None, cancelled_by: str | None = None,
                  result: Any = None, describe: Callable[[Any], dict] | None = None) -> None:
    finished = datetime.now(timezone.utc)
    record.update(
        started_at=started_at,
        finished_at=finished,
        outcome=outcome,
        http_status=http_status,
        error_stage=error_stage if outcome != "ok" else None,
        error_class=type(error).__name__ if error is not None else None,
        error_message=scrub(str(getattr(error, "detail", None) or error))[:_ERROR_TEXT_LIMIT]
        if error is not None else None,
        cancelled_by=cancelled_by,
        backend_commit=backend_commit(),
        hosted=bool(os.environ.get("RENDER")),
        peak_rss_mb=_rss_mb(),
        settings_snapshot=settings_snapshot(),
    )
    if result is not None and describe is not None:
        record["result"] = result
        record["describe"] = describe
    submit(record)


# ----------------------------------------------------------------------
# Result descriptions: route results -> table rows
# ----------------------------------------------------------------------

def _g(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _flag_value(command: list, flag: str) -> int | None:
    try:
        i = command.index(flag)
        return int(command[i + 1])
    except (ValueError, IndexError, TypeError):
        return None


def _stuck_at(text: str | None) -> int | None:
    m = re.search(r"([01])\s*$", str(text or ""))
    return int(m.group(1)) if m else None


def _usage_rows(usage: dict | None, provider: str | None, model: str | None) -> list[dict]:
    if not usage or (usage.get("enabled") is False and not usage.get("api_calls")):
        return []
    provider = usage.get("provider") or provider
    model = usage.get("model") or model
    rows = []
    for stage, s in (usage.get("by_stage") or {}).items():
        rows.append({
            "stage": stage, "provider": provider, "model": model,
            "api_calls": s.get("calls") or 0, "cached_hits": s.get("cached_hits") or 0,
            "prompt_tokens": s.get("prompt_tokens_est"),
            "response_tokens": s.get("response_tokens_est"),
            "total_tokens": s.get("tokens_est"),
            "tokens_exact": bool(usage.get("tokens_exact")),
        })
    if not rows and (usage.get("api_calls") or usage.get("cached_hits")):
        rows.append({
            "stage": "total", "provider": provider, "model": model,
            "api_calls": usage.get("api_calls") or 0, "cached_hits": usage.get("cached_hits") or 0,
            "prompt_tokens": usage.get("prompt_tokens_est"),
            "response_tokens": usage.get("response_tokens_est"),
            "total_tokens": usage.get("total_tokens_est"),
            "tokens_exact": bool(usage.get("tokens_exact")),
            "duration_s": usage.get("total_duration_s"),
        })
    elif rows and usage.get("total_duration_s") is not None and len(rows) == 1:
        rows[0]["duration_s"] = usage.get("total_duration_s")
    return rows


def describe_atpg(analysis: Any) -> dict[str, Any]:
    """Rows for an ATPG run (an ``AtpgAnalysis`` or its ``to_dict()``)."""
    p = analysis.to_dict() if hasattr(analysis, "to_dict") else analysis
    circuit = p.get("circuit") or {}
    notes = p.get("notes") or []
    cuts = 0
    for note in notes:
        m = re.search(r"describes (\d+) combinational feedback", str(note))
        if m:
            cuts = int(m.group(1))
    ff = circuit.get("flip_flops") or 0
    out: dict[str, Any] = {
        "circuit": {
            "name": circuit.get("name"),
            "primary_inputs": circuit.get("primary_inputs"),
            "primary_outputs": circuit.get("primary_outputs"),
            "gates": circuit.get("gates"),
            "flip_flops": ff,
            "logic_depth": circuit.get("logic_depth"),
            "loop_cuts": cuts,
            "circuit_kind": "sequential" if ff else "combinational",
            "synth_notes": [n for n in notes if "synthes" in str(n).lower() or "RTL" in str(n)][:20] or None,
        },
        "run": {"job_id": p.get("job_id"), "mode": p.get("mode")},
    }
    cpu = p.get("cpu_time") or {}
    usage = p.get("llm_usage") or {}
    seq = p.get("sequential")

    if seq:
        usage = seq.get("llm_usage") or usage
        comp = seq.get("comparison")
        engine_runs = ({"deterministic": comp["baseline"], "llm_assisted": comp["llm_assisted"]}
                       if comp else {"deterministic": seq.get("analysis") or {}})
        rows, faults = [], {}
        for kind, a in engine_runs.items():
            r = a.get("results") or {}
            params = a.get("parameters") or {}
            timing = a.get("timing") or {}
            rows.append({
                "kind": kind,
                "time_budget_s": params.get("time_budget_s"),
                "max_frames": params.get("max_frames") or params.get("max_sequence_length"),
                "random_sequences": params.get("random_sequences"),
                "parameters": params or None,
                **{k: r.get(k) for k in (
                    "total_faults", "detected_faults", "undetected_faults", "aborted_faults",
                    "podem_exhausted_faults", "not_confirmed_faults", "not_attempted_faults",
                    "fault_coverage", "sequence_count", "total_clock_cycles", "max_sequence_length")},
                "stopped_by": a.get("stopped_by"),
                "phases": a.get("phases") or None,
                "extraction_s": cpu.get("extraction_s"),
                "llm_s": timing.get("llm_s"),
                "atpg_s": timing.get("total_s"),
                "total_s": _g(cpu, "by_run", kind, "total_s"),
            })
            faults[kind] = [
                {"gate": f.get("node") or f.get("fault"), "stuck_at": f.get("stuck_at"),
                 "status": f.get("status") or "undetected"}
                for f in (a.get("fault_details") or [])[:MAX_FAULT_ROWS]
                if f.get("stuck_at") in (0, 1)
            ]
        out["seq_runs"] = rows
        out["seq_faults"] = faults
        if comp:
            out["seq_comparison"] = {
                "baseline_coverage": _g(comp, "baseline", "results", "fault_coverage"),
                "llm_coverage": _g(comp, "llm_assisted", "results", "fault_coverage"),
                "coverage_delta": comp.get("coverage_delta"),
                "additional_faults_detected": len(comp.get("additional_faults_detected") or []),
                "additional_sequences": comp.get("additional_sequences"),
                "additional_clock_cycles": comp.get("additional_clock_cycles"),
            }
        main = engine_runs.get("llm_assisted") or engine_runs["deterministic"]
        out["run"].update(fault_coverage=_g(main, "results", "fault_coverage"),
                          patterns=_g(main, "results", "sequence_count"))
        out["artifacts"] = [{"engine_kind": None, "artifact_key": k, "filename": v}
                            for k, v in (seq.get("artifacts") or {}).items()]
    else:
        runs = p.get("runs") or {}
        rows, faults, artifacts = [], {}, []
        for kind, r in runs.items():
            res = r.get("results") or {}
            params = r.get("parameters") or {}
            timing = r.get("timing") or {}
            sim = r.get("pattern_coverage") or {}
            command = r.get("command") or []
            gates = r.get("targeted_gates") or []
            rows.append({
                "kind": kind,
                "attempt": max(1, len(r.get("attempts") or [])),
                "backtrack_limit": params.get("backtrack_limit") or _flag_value(command, "-b"),
                "backtrack_limit_phase2": _flag_value(command, "-B"),
                "static_learning": "-L" in command,
                "random_pattern_limit": params.get("random_pattern_limit"),
                "random_seed": params.get("random_seed"),
                "compaction_mode": params.get("compaction_mode"),
                "targeted_gate_count": len(gates) if kind == "targeted" else None,
                "targeted_fault_count": 2 * len(gates) if kind == "targeted" else None,
                "collapsed_faults": res.get("collapsed_faults"),
                "detected_faults": res.get("detected_faults"),
                "redundant_faults": res.get("redundant_faults"),
                "aborted_faults": res.get("aborted_faults"),
                "fault_coverage": res.get("fault_coverage"),
                "fault_efficiency": res.get("fault_efficiency"),
                "patterns_before_compact": res.get("patterns_before_compaction"),
                "patterns_after_compact": res.get("patterns_after_compaction"),
                "backtrackings": res.get("backtrackings"),
                "sim_fault_count": sim.get("total_faults"),
                "sim_detected": sim.get("detected_faults"),
                "sim_coverage": sim.get("coverage"),
                "cpu_init_s": timing.get("initialization"),
                "cpu_fault_sim_s": timing.get("fault_simulation"),
                "cpu_fan_s": timing.get("fan"),
                "cpu_total_s": timing.get("total"),
                "wall_s": _g(cpu, "by_run", kind, "total_s"),
                "memory_mb": r.get("memory_mb"),
                "exit_code": r.get("exit_code"),
            })
            faults[kind] = [
                {"gate": f.get("gate"), "stuck_at": _stuck_at(f.get("fault_type") or f.get("fault_name")),
                 "status": f.get("status")}
                for f in (r.get("faults") or [])[:MAX_FAULT_ROWS]
            ]
            artifacts += [{"engine_kind": kind, "artifact_key": k, "filename": v}
                          for k, v in (r.get("artifacts") or {}).items()]
        out["comb_runs"] = rows
        out["comb_faults"] = faults
        out["artifacts"] = artifacts
        primary = runs.get("non_targeted") or runs.get("targeted") or {}
        out["run"].update(fault_coverage=_g(primary, "results", "fault_coverage"),
                          patterns=_g(primary, "results", "patterns_after_compaction"))
        comp = p.get("comparison")
        if comp:
            out["comb_comparison"] = {
                "targeted_coverage": comp.get("targeted_coverage"),
                "non_targeted_coverage": comp.get("non_targeted_coverage"),
                "coverage_delta": comp.get("coverage_delta"),
                "targeted_circuit_wide_coverage": comp.get("targeted_circuit_wide_coverage"),
                "targeted_patterns": comp.get("targeted_patterns"),
                "non_targeted_patterns": comp.get("non_targeted_patterns"),
                "pattern_reduction_pct": comp.get("pattern_reduction_pct"),
                "targeted_runtime_s": comp.get("targeted_runtime"),
                "non_targeted_runtime_s": comp.get("non_targeted_runtime"),
                "targeted_faults": comp.get("targeted_faults"),
                "non_targeted_faults": comp.get("non_targeted_faults"),
                "targeted_fault_share_pct": comp.get("targeted_fault_share_pct"),
                "targeted_sim_coverage": comp.get("targeted_simulated_coverage"),
                "non_targeted_sim_coverage": comp.get("non_targeted_simulated_coverage"),
                "sim_coverage_delta": comp.get("simulated_coverage_delta"),
                "sim_fault_count": comp.get("simulated_fault_count"),
            }
        t = p.get("targeting")
        if t:
            out["gate_selection"] = {
                "strategy": t.get("strategy") or "unknown",
                "used_llm": bool(t.get("used_llm")),
                "cached": bool(t.get("cached")),
                "gate_count": t.get("gate_count") or len(t.get("gates") or []),
                "structural_suspect_count": len(t.get("structural_suspects") or []),
                "toggle_patterns": t.get("toggle_patterns"),
                "toggle_seed": t.get("toggle_seed"),
                "extraction_s": cpu.get("extraction_s"),
                "llm_s": cpu.get("llm_s"),
                "gates": t.get("gates"),
                "notes": t.get("notes"),
            }

    out["llm_usage"] = _usage_rows(usage, None, None)
    out["run"].update(
        llm_api_calls=usage.get("api_calls") if usage else None,
        llm_total_tokens=usage.get("total_tokens_est") if usage else None,
    )
    v = p.get("verdict")
    if v:
        out["verdict"] = {
            "verdict": v.get("verdict"),
            "confidence": v.get("confidence") or 0.0,
            "suspect_count": len(v.get("suspects") or []),
            "summary": v.get("summary"),
            "caveats": v.get("caveats"),
        }
        out["suspects"] = [
            {"rank": i, "gate": s.get("gate"), "confidence": s.get("confidence") or 0.0,
             "evidence": s.get("evidence") or "structural", "atpg_status": s.get("atpg_status"),
             "reasons": s.get("reasons"), "activating_vectors": len(s.get("activating_vectors") or [])}
            for i, s in enumerate(v.get("suspects") or [], start=1)
        ]
        out["run"]["trojan_verdict"] = v.get("verdict")
    return out


def describe_parse(result: dict) -> dict[str, Any]:
    return {"circuit": {
        "name": result.get("name"),
        "primary_inputs": result.get("primary_inputs"),
        "primary_outputs": result.get("primary_outputs"),
        "gates": result.get("gates"),
        "logic_depth": result.get("levels"),
    }}


def describe_features(fs: Any) -> dict[str, Any]:
    gates = list(getattr(fs, "gate_features", []) or [])
    feature_count = None
    if gates:
        try:
            feature_count = len(gates[0].to_dict())
        except Exception:  # noqa: BLE001
            pass
    ms = getattr(fs, "extraction_time_ms", None)
    return {
        "circuit": {"name": getattr(fs, "circuit_name", None), "gates": len(gates) or None},
        "feature_extraction": {"gate_count": len(gates), "feature_count": feature_count,
                               "extraction_s": round(ms / 1000, 3) if ms else None},
    }


def describe_trojan(result: dict) -> dict[str, Any]:
    return {"trojan_generation": {
        "variants_requested": result.get("variants_requested") or 0,
        "variants_generated": result.get("variants_generated") or 0,
        "strategy": result.get("strategy") or "unknown",
        "seed": result.get("seed"),
        "trigger_inputs": result.get("num_trigger_inputs"),
        "trigger_type": result.get("trigger_type"),
        "payload_type": result.get("payload_type"),
        "verify_activation": bool(result.get("verify_activation")),
        "activation_verified": result.get("activation_verified"),
    }}


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------

_RUN_COLUMNS = (
    "progress_id", "job_id", "run_type", "endpoint", "label", "received_at", "started_at",
    "finished_at", "queue_wait_s", "duration_s", "outcome", "http_status", "error_stage",
    "error_class", "error_message", "cancelled_by", "circuit_id", "upload_filename",
    "circuit_kind", "mode", "requested_patterns", "backtrack_limit", "full_scan", "use_llm",
    "llm_provider", "llm_model", "llm_base_url_host", "llm_key_source", "llm_fallback_keys",
    "fault_coverage", "patterns", "llm_api_calls", "llm_total_tokens", "trojan_verdict",
    "backend_commit", "hosted", "peak_rss_mb", "settings_snapshot", "client_hash",
)

_CIRCUIT_SIZE_COLUMNS = ("primary_inputs", "primary_outputs", "gates", "flip_flops", "logic_depth",
                         "loop_cuts", "synth_notes")


def _adapt(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value


def _insert(cur, table: str, row: dict[str, Any], returning: str | None = None) -> Any:
    from psycopg import sql

    row = {k: v for k, v in row.items() if v is not None}
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    if returning:
        query = query + sql.SQL(" RETURNING {}").format(sql.Identifier(returning))
    cur.execute(query, [_adapt(v) for v in row.values()])
    return cur.fetchone()[0] if returning else None


def _upsert_circuit(cur, meta: dict[str, Any], parsed: dict[str, Any], succeeded: bool) -> int | None:
    sha = meta.get("content_sha256")
    if not sha:
        return None
    kind = parsed.get("circuit_kind") or meta.get("circuit_kind") or "combinational"
    name = parsed.get("name") or meta.get("name_hint") or meta.get("upload_filename") or "unknown"
    row = {
        "content_sha256": sha, "name": name,
        "display_name": meta.get("trojan_id") or name,
        "source_suite": meta.get("source_suite") or "unknown",
        "benchmark_family": meta.get("benchmark_family"),
        "trojan_label": meta.get("trojan_label") or "unknown",
        "trojan_id": meta.get("trojan_id"),
        "design_level": meta.get("design_level") or "gate",
        "input_format": meta.get("input_format") or "verilog_gate",
        "circuit_kind": kind,
        "has_scan": bool(meta.get("has_scan")),
        "file_count": meta.get("file_count") or 1,
        "upload_bytes": meta.get("upload_bytes"),
        "synthesized": bool(meta.get("synthesized")),
        **{k: parsed.get(k) for k in _CIRCUIT_SIZE_COLUMNS},
    }
    cols = [k for k, v in row.items() if v is not None]
    updates = [f"{c} = COALESCE(EXCLUDED.{c}, circuits.{c})" for c in _CIRCUIT_SIZE_COLUMNS]
    if succeeded:
        updates += ["circuit_kind = EXCLUDED.circuit_kind", "name = EXCLUDED.name"]
    from psycopg import sql

    query = sql.SQL("INSERT INTO circuits ({}) VALUES ({}) ON CONFLICT (content_sha256) DO UPDATE SET "
                    + ", ".join(updates) + " RETURNING id").format(
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    cur.execute(query, [_adapt(row[c]) for c in cols])
    return cur.fetchone()[0]


def _ground_truth(verdict: str | None, label: str | None) -> bool | None:
    if label == "trojan_inserted":
        return verdict is not None and verdict != "no_trojan_indicated"
    if label == "trojan_free":
        return verdict == "no_trojan_indicated"
    return None


def write(conn, record: dict[str, Any]) -> str:
    """Write one run and its details in a single transaction; returns the run id."""
    meta = record.get("meta") or {}
    details = record.get("details") or {}
    run_extra = details.get("run") or {}
    parsed = details.get("circuit") or {}
    succeeded = record.get("outcome") == "ok"
    received, started, finished = (record.get("received_at"), record.get("started_at"),
                                   record.get("finished_at"))

    with conn.cursor() as cur:
        circuit_id = _upsert_circuit(cur, meta, parsed, succeeded)
        row = {k: meta.get(k) for k in _RUN_COLUMNS if k in meta}
        row.update({k: record.get(k) for k in _RUN_COLUMNS if record.get(k) is not None})
        row.update({k: v for k, v in run_extra.items() if v is not None})
        row["circuit_id"] = circuit_id
        if parsed.get("circuit_kind"):
            row["circuit_kind"] = parsed["circuit_kind"]
        elif meta.get("circuit_kind") and circuit_id is not None:
            row.setdefault("circuit_kind", meta["circuit_kind"])
        if started and received:
            row["queue_wait_s"] = round((started - received).total_seconds(), 2)
        if finished and (started or received):
            row["duration_s"] = round((finished - (started or received)).total_seconds(), 2)
        run_id = _insert(cur, "runs", {k: v for k, v in row.items() if k in _RUN_COLUMNS}, "id")

        comb_ids = {}
        for r in details.get("comb_runs") or []:
            comb_ids[r["kind"]] = _insert(cur, "comb_atpg_runs", {"run_id": run_id, **r}, "id")
        seq_ids = {}
        for r in details.get("seq_runs") or []:
            seq_ids[r["kind"]] = _insert(cur, "seq_atpg_runs", {"run_id": run_id, **r}, "id")
        for kind, faults in (details.get("comb_faults") or {}).items():
            _copy_faults(cur, "comb_run_id", comb_ids.get(kind), faults)
        for kind, faults in (details.get("seq_faults") or {}).items():
            _copy_faults(cur, "seq_run_id", seq_ids.get(kind), faults)

        for table, key in (("gate_selections", "gate_selection"), ("comb_comparisons", "comb_comparison"),
                           ("seq_comparisons", "seq_comparison"),
                           ("trojan_generations", "trojan_generation"),
                           ("feature_extractions", "feature_extraction"),
                           ("ranking_runs", "ranking"), ("benchmark_jobs", "benchmark")):
            if details.get(key):
                _insert(cur, table, {"run_id": run_id, **details[key]})

        for u in details.get("llm_usage") or []:
            _insert(cur, "llm_usage", {"run_id": run_id, **u})

        v = details.get("verdict")
        if v:
            label = meta.get("trojan_label")
            truth = label if label in ("trojan_free", "trojan_inserted") else None
            _insert(cur, "trojan_verdicts", {
                "run_id": run_id, **v, "ground_truth_label": truth,
                "verdict_correct": _ground_truth(v.get("verdict"), truth),
            })
            for s in details.get("suspects") or []:
                _insert(cur, "suspect_gates", {"run_id": run_id, **s})

        for a in details.get("artifacts") or []:
            _insert(cur, "artifacts", {"run_id": run_id, **a})
    conn.commit()
    return str(run_id)


def _copy_faults(cur, column: str, engine_id: int | None, faults: list[dict]) -> None:
    rows = [f for f in faults if engine_id and f.get("gate") and f.get("stuck_at") in (0, 1)]
    if not rows:
        return
    with cur.copy(f"COPY fault_results ({column}, gate, stuck_at, status) FROM STDIN") as copy:
        for f in rows:
            copy.write_row((engine_id, str(f["gate"]), int(f["stuck_at"]), str(f.get("status") or "unknown")))


def request_meta(text: str, filename: str, raw: bytes | None = None, **extra: Any) -> dict[str, Any]:
    """Upload classification plus request columns; {} when recording is off. Never raises."""
    if not enabled():
        return {}
    meta: dict[str, Any] = {}
    try:
        meta.update(upload_info(text, filename, raw))
    except Exception as exc:  # noqa: BLE001
        log.warning("run_db: classifying the upload failed: %s", exc)
        meta["upload_filename"] = filename
    llm = extra.pop("llm", None)
    if llm:
        try:
            meta.update(llm_info(**llm))
        except Exception as exc:  # noqa: BLE001
            log.warning("run_db: resolving the LLM failed: %s", exc)
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


def record_simple(*, run_type: str, endpoint: str, label: str, received_at: datetime,
                  request=None, meta: dict[str, Any] | None = None, outcome: str = "ok",
                  http_status: int = 200, error: BaseException | None = None,
                  details: dict[str, Any] | None = None) -> None:
    """Record a run that did not go through ``run_tracked`` (ranking, benchmark start)."""
    if not enabled():
        return
    try:
        record = base_record(run_type=run_type, endpoint=endpoint, label=label,
                             progress_id=None, request=request, meta=meta)
        record["received_at"] = received_at
        finish_record(record, started_at=received_at, outcome=outcome, http_status=http_status,
                      error=error, result=details or {}, describe=lambda d: d)
    except Exception as exc:  # noqa: BLE001
        _note("failed", f"{type(exc).__name__}: {exc}")
