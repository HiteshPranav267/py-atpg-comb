from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from pyatpg.api.services.trojan_service import TrojanService

router = APIRouter(prefix="/api/trojan", tags=["trojan"])


@router.post("/generate")
async def generate_dataset(
    file: UploadFile = File(...),
    variants: int = Form(10),
    strategy: str = Form("random"),
):
    """Generate a trojan dataset from an uploaded circuit file."""
    try:
        content = await file.read()
        text = content.decode("utf-8")
        name = (file.filename or "circuit.v").rsplit('.', 1)[0]
        result = TrojanService.generate_dataset(text, name, variants, strategy)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
