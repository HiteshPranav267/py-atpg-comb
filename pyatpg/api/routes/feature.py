from fastapi import APIRouter, UploadFile, File, HTTPException

from pyatpg.api.concurrency import run_heavy
from pyatpg.core.rtl import decode_upload
from typing import Optional

from pyatpg.api.services.feature_service import FeatureService
from pyatpg.api.schemas import FeatureSetSchema

router = APIRouter(prefix="/api/features", tags=["features"])


@router.post("/extract", response_model=FeatureSetSchema)
async def extract_features(file: UploadFile = File(...)):
    """Upload Verilog and return the full FeatureSet."""
    try:
        content = await file.read()
        text, name = decode_upload(file.filename or "unknown.v", content)

        fs = await run_heavy(FeatureService.extract_features, text, name)
        return FeatureSetSchema(
            circuit_name=fs.circuit_name,
            gate_features=[gf.to_dict() for gf in fs.gate_features],
            atpg_coverage=fs.atpg_coverage,
            extraction_time_ms=fs.extraction_time_ms,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
