"""Runtime-adjustable server settings: admin overrides on top of the environment.

Each setting is read through :func:`get` at the moment it is needed, so an
admin can change it on a running server (``/api/admin/settings``) without a
redeploy. Overrides live in memory only: a restart returns every setting to
its environment variable, or to the default when that is unset.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Setting:
    key: str
    env: str
    kind: type
    default: Any
    label: str
    help: str
    #: Applies only on the hosted backend (Render); 0 elsewhere unless set.
    hosted_only: bool = False
    minimum: float = 0


SETTINGS: dict[str, Setting] = {s.key: s for s in [
    Setting("maintenance", "PYATPG_MAINTENANCE", bool, False, "Maintenance mode",
            "Reject new analyses (runs already in progress continue)."),
    Setting("max_concurrent_runs", "PYATPG_MAX_CONCURRENT_RUNS", int, 1, "Concurrent analyses",
            "How many analyses may run at once; the rest queue.", minimum=1),
    Setting("max_gates", "PYATPG_MAX_GATES", int, 60000, "Max design size (gates + flip-flops)",
            "Larger designs are rejected before they can exhaust memory. 0 = unlimited.",
            hosted_only=True),
    Setting("yosys_max_mb", "PYATPG_YOSYS_MAX_MB", int, 300, "Yosys memory limit (MB)",
            "RTL synthesis is stopped above this. 0 = unlimited.", hosted_only=True),
    Setting("atalanta_timeout_s", "PYATPG_ATALANTA_TIMEOUT", int, 600, "Atalanta timeout (s)",
            "Per Atalanta run.", minimum=10),
    Setting("atalanta_seed", "PYATPG_ATALANTA_SEED", int, 1, "Atalanta random seed",
            "Fixed so repeated runs give identical patterns; 0 = seed from the clock "
            "(Atalanta's own default).", minimum=0),
    Setting("llm_batches", "PYATPG_LLM_BATCHES", int, 4, "LLM selection batches",
            "When a circuit's gates do not fit one LLM request, review them in this many "
            "requests, each carrying the previous shortlist forward (1 = one trimmed "
            "request). More batches see more gates but take longer on a rate-limited key.",
            minimum=1),
    Setting("detector_in_targeting", "PYATPG_DETECTOR_IN_TARGETING", bool, True,
            "Detector feeds the targeted list",
            "Off = the targeted fault list is the LLM's picks alone (no detector gates "
            "forced in, no marks shown to it), and the 'without LLM' baseline is the plain "
            "heuristic -- for measuring what the LLM finds by itself. The verdict still "
            "uses the detector."),
    Setting("llm_timeout_s", "PYATPG_LLM_TIMEOUT", float, 90.0, "LLM request timeout (s)",
            "Per LLM request (retries and backup keys come on top).", minimum=1),
    Setting("seq_time_budget_s", "PYATPG_SEQ_TIME_BUDGET", float, 40.0,
            "Sequential search budget (s)", "Search time per sequential ATPG run.", minimum=1),
    Setting("yosys_timeout_s", "PYATPG_YOSYS_TIMEOUT", float, 3600.0, "Yosys timeout (s)",
            "RTL synthesis time limit.", minimum=10),
]}

_lock = threading.Lock()
_overrides: dict[str, Any] = {}


def _coerce(setting: Setting, value: Any) -> Any:
    if setting.kind is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    value = setting.kind(value)
    if value < setting.minimum and not (setting.hosted_only and value == 0):
        raise ValueError(f"{setting.label} must be at least {setting.minimum:g}.")
    return value


def _from_env(setting: Setting) -> Any:
    raw = os.environ.get(setting.env, "").strip()
    if raw:
        try:
            return _coerce(setting, raw)
        except (TypeError, ValueError):
            pass
    if setting.hosted_only and not os.environ.get("RENDER"):
        return 0
    return setting.default


def get(key: str) -> Any:
    setting = SETTINGS[key]
    with _lock:
        if key in _overrides:
            return _overrides[key]
    return _from_env(setting)


def set_override(key: str, value: Any) -> Any:
    """Override ``key`` until restart (None clears the override)."""
    setting = SETTINGS[key]
    with _lock:
        if value is None:
            _overrides.pop(key, None)
            return None
        _overrides[key] = _coerce(setting, value)
        return _overrides[key]


def describe() -> list[dict[str, Any]]:
    """Every setting with its effective value and where that value comes from."""
    rows = []
    for key, s in SETTINGS.items():
        with _lock:
            overridden = key in _overrides
        rows.append({
            "key": key,
            "label": s.label,
            "help": s.help,
            "type": s.kind.__name__,
            "value": get(key),
            "source": "admin override" if overridden
            else ("environment" if os.environ.get(s.env, "").strip() else "default"),
            "env": s.env,
        })
    return rows
