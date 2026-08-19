from typing import Any, Dict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from pyatpg.atpg import (
    AnalysisMode,
    AtalantaExecutionError,
    AtalantaNotAvailable,
    AtpgAnalysisService,
    NoTargetableGates,
)
from pyatpg.atpg.workspace import JobWorkspace

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
) -> Dict[str, Any]:
    """Run an ATPG analysis over an uploaded circuit.

    Combinational circuits run through Atalanta; circuits with flip-flops
    are routed automatically to the native sequential (time-frame-expansion)
    engine -- the response's `sequential` field is populated instead of
    `runs`/`comparison`/`targeting` in that case. `mode`/`patterns` only
    apply to the combinational path.

    `api_key` is optional and applies to this request only, letting a
    credential be changed without restarting the service. It is never
    logged, stored with the job, or returned in the response.
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
        analysis = _service.run(
            source_text=source_text,
            filename=file.filename or "circuit.v",
            mode=selected_mode,
            toggle_patterns=patterns,
            use_llm=use_llm,
            api_key=api_key or None,
        )
    except (ValueError, NoTargetableGates) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AtalantaNotAvailable as exc:
        # The deployment has no usable engine; this is a server-side problem.
        raise HTTPException(status_code=503, detail=str(exc))
    except AtalantaExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return analysis.to_dict()


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
