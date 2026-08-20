from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Dict, Any

from pyatpg.api.services.circuit_service import CircuitService
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/circuit", tags=["circuit"])


@router.post("/parse")
async def parse_circuit(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Parse an uploaded Verilog file and return circuit metadata."""
    try:
        content = await file.read()
        text = content.decode("utf-8")
        name = file.filename or "unknown.v"
        result = CircuitService.parse_verilog(text, name)
        get_analytics_store().record(
            "circuit_parsed",
            circuit_name=name,
            gates=result.get("gates"),
        )
        return {"status": "success", "circuit": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
