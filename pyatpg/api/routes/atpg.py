from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Any, Dict

from pyatpg.api.services.atpg_service import AtpgService

router = APIRouter(prefix="/api/atpg", tags=["atpg"])


@router.post("/run")
async def run_atpg(
    file: UploadFile = File(...),
    patterns: int = Form(3000),
    use_llm: bool = Form(True),
) -> Dict[str, Any]:
    """Run the full structural -> AI -> Atalanta -> validation pipeline on an uploaded circuit."""
    try:
        content = await file.read()
        text = content.decode("utf-8")
        name = file.filename or "circuit.v"
        return AtpgService.run(text, name, patterns=patterns, use_llm=use_llm)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
