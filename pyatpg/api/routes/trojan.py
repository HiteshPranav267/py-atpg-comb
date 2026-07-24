from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pyatpg.api.services.trojan_service import TrojanService

router = APIRouter(prefix="/api/trojan", tags=["trojan"])


class TrojanGenerateRequest(BaseModel):
    circuit_path: str
    variants: int = 10
    strategy: str = "random"


@router.post("/generate")
def generate_dataset(req: TrojanGenerateRequest):
    """Generate a trojan dataset from a circuit file."""
    try:
        result = TrojanService.generate_dataset(req.circuit_path, req.variants, req.strategy)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
