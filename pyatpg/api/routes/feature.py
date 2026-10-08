from fastapi import APIRouter, UploadFile, File, HTTPException, Request

from pyatpg.api.concurrency import run_tracked
from pyatpg.core import run_db
from pyatpg.core.rtl import decode_upload
from typing import Optional

from pyatpg.api.services.feature_service import FeatureService
from pyatpg.api.schemas import FeatureSetSchema

router = APIRouter(prefix="/api/features", tags=["features"])


@router.post("/extract", response_model=FeatureSetSchema)
async def extract_features(request: Request, file: UploadFile = File(...)):
    """Upload Verilog and return the full FeatureSet."""
    try:
        content = await file.read()
        text, name = decode_upload(file.filename or "unknown.v", content)

        fs = await run_tracked(request, None, FeatureService.extract_features, text, name,
                               label=f"Feature extraction: {name}",
                               db_run_type="feature_extract",
                               db_meta=run_db.request_meta(text, name, content),
                               db_describe=run_db.describe_features)
        return FeatureSetSchema(
            circuit_name=fs.circuit_name,
            gate_features=[gf.to_dict() for gf in fs.gate_features],
            atpg_coverage=fs.atpg_coverage,
            extraction_time_ms=fs.extraction_time_ms,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
