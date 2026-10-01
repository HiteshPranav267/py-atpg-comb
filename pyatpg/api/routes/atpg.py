from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pyatpg.atpg import (
    AnalysisMode,
    AtalantaExecutionError,
    AtalantaNotAvailable,
    AtpgAnalysisService,
    LLMUnavailableError,
    NoTargetableGates,
)
from pyatpg.atpg.workspace import JobWorkspace
from pyatpg.ranking.providers import parse_llm_fallbacks
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/atpg", tags=["atpg"])

_service = AtpgAnalysisService()

#: Content types for the artifacts a job can produce. Everything Atalanta
#: writes is plain text, so downloads are served as such.
_ARTIFACT_MEDIA_TYPE = "text/plain; charset=utf-8"


@router.get("/modes")
def list_modes() -> list[Dict[str, str]]:
    """The selectable analysis modes and their display labels.

    These modes apply to combinational circuits (run through Atalanta). A
    circuit with flip-flops bypasses mode selection entirely -- POST /run
    routes it to the native sequential ATPG engine automatically.
    """
    return [
        {
            "value": mode.value,
            "label": mode.label,
            "description": description,
        }
        for mode, description in (
            (
                AnalysisMode.TARGETED,
                "Select gates with the LLM (or the structural heuristic when no key is "
                "configured) and run the ATPG tool against only those faults.",
            ),
            (
                AnalysisMode.NON_TARGETED,
                "Run the ATPG tool over the circuit's full collapsed fault list, with no "
                "gate selection applied.",
            ),
            (
                AnalysisMode.COMPARE,
                "Run both and compare them side by side.",
            ),
        )
    ]


@router.post("/run")
async def run_atpg(
    file: UploadFile = File(...),
    mode: str = Form(AnalysisMode.COMPARE.value),
    patterns: int = Form(3000),
    use_llm: bool = Form(True),
    api_key: str = Form(""),
    backtrack_limit: int | None = Form(None),
    llm_provider: str = Form(""),
    llm_model: str = Form(""),
    llm_base_url: str = Form(""),
    full_scan: bool = Form(False),
    llm_fallbacks: str = Form(""),
) -> Dict[str, Any]:
    """Run an ATPG analysis over an uploaded circuit.

    Combinational circuits run through Atalanta; circuits with flip-flops
    are routed automatically to the native sequential (time-frame-expansion)
    engine -- the response's `sequential` field is populated instead of
    `runs`/`comparison`/`targeting` in that case. `mode`/`patterns` only
    apply to the combinational path.

    `api_key` is optional and applies to this request only, letting a
    credential be changed without restarting the service. It is never
    logged, stored with the job, or returned in the response. `llm_provider`
    names the vendor the key belongs to (see `GET /api/llm/providers`);
    blank or "auto" sniffs it from the key's prefix. `llm_model` and
    `llm_base_url` override the provider's default model and, for
    OpenAI-compatible providers, its endpoint.

    `llm_fallbacks` is an optional JSON list of up to four backup
    credentials, each `{"api_key", "provider"?, "model"?, "base_url"?}`.
    When a key hits its rate or quota limit (or is rejected) mid-run, the
    next one takes over; like `api_key` they are never logged or returned.

    `backtrack_limit` is optional and forwarded to Atalanta's own `-b` flag
    on every run this request makes (combinational path only). Omitted, it
    leaves Atalanta's built-in default (10) in effect -- raise it to see
    whether a fault reported as aborted is genuinely undetectable or just
    ran out of search budget.

    `full_scan` applies to circuits with flip-flops: when true they are
    tested under the full-scan model (flip-flop Q/D become pseudo
    inputs/outputs) through the combinational Atalanta path instead of the
    sequential engine.
    """
    try:
        selected_mode = AnalysisMode(mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode {mode!r}. Expected one of: "
            + ", ".join(m.value for m in AnalysisMode),
        )

    try:
        content = await file.read()
        source_text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Circuit file must be UTF-8 text.")

    try:
        fallbacks = parse_llm_fallbacks(llm_fallbacks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        analysis = _service.run(
            source_text=source_text,
            filename=file.filename or "circuit.v",
            mode=selected_mode,
            toggle_patterns=patterns,
            use_llm=use_llm,
            api_key=api_key or None,
            backtrack_limit=backtrack_limit,
            llm_provider=llm_provider or None,
            llm_model=llm_model or None,
            llm_base_url=llm_base_url or None,
            full_scan=full_scan,
            llm_fallbacks=fallbacks,
        )
    except (ValueError, NoTargetableGates) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AtalantaNotAvailable as exc:
        # The deployment has no usable engine; this is a server-side problem.
        raise HTTPException(status_code=503, detail=str(exc))
    except AtalantaExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LLMUnavailableError as exc:
        # The model could not be consulted (no credits, rate limited, the
        # provider returned a 5xx, key rejected). The user asked for an
        # LLM-assisted run, so this is reported as a failure of that run --
        # not silently downgraded to the offline heuristic. 502 because the
        # failure is upstream of this service; the detail says exactly why.
        raise HTTPException(status_code=502, detail=str(exc))

    payload = analysis.to_dict()
    _record_atpg_run(payload)
    return payload


def _record_atpg_run(payload: dict) -> None:
    """Log one ATPG run for the in-app analytics page.

    Best-effort only: recording happens after the real result is already
    computed and about to be returned, so a failure here must never affect
    the response.
    """
    try:
        sequential = payload.get("sequential")
        if sequential is not None:
            results = sequential["analysis"]["results"]
            usage = sequential.get("llm_usage") or {}
            get_analytics_store().record(
                "atpg_run",
                circuit_kind="sequential",
                circuit_name=sequential["circuit"].get("name"),
                mode=payload.get("mode"),
                fault_coverage=results.get("fault_coverage"),
                llm_api_calls=usage.get("api_calls"),
                llm_tokens_est=usage.get("total_tokens_est"),
            )
            return

        runs = payload.get("runs") or {}
        primary = runs.get("non_targeted") or runs.get("targeted")
        coverage = primary["results"].get("fault_coverage") if primary else None
        usage = payload.get("llm_usage") or {}
        get_analytics_store().record(
            "atpg_run",
            circuit_kind="combinational",
            circuit_name=payload.get("circuit", {}).get("name"),
            mode=payload.get("mode"),
            fault_coverage=coverage,
            llm_api_calls=usage.get("api_calls") if usage.get("enabled") else 0,
            llm_tokens_est=usage.get("total_tokens_est") if usage.get("enabled") else None,
        )
    except Exception:  # noqa: BLE001 - analytics must never break a real response
        pass


@router.get("/jobs/{job_id}/artifacts/{filename}")
def download_artifact(job_id: str, filename: str) -> FileResponse:
    """Download one raw artifact produced by a previous run.

    Both the job id and the filename are validated against the workspace so
    neither can be used to reach outside the job directory.
    """
    workspace = JobWorkspace.open_existing(job_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")

    path = workspace.resolve_artifact(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Unknown artifact for this job.")

    return FileResponse(path, media_type=_ARTIFACT_MEDIA_TYPE, filename=path.name)
