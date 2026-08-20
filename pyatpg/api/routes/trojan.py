import io
import os
import zipfile

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse

from pyatpg.api.services.trojan_service import TrojanService
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/trojan", tags=["trojan"])


@router.post("/generate")
async def generate_dataset(
    file: UploadFile = File(...),
    variants: int = Form(10),
    strategy: str = Form("random"),
):
    """Generate a trojan dataset from an uploaded circuit file and return it as a zip."""
    try:
        content = await file.read()
        text = content.decode("utf-8")
        name = (file.filename or "circuit.v").rsplit('.', 1)[0]
        result = TrojanService.generate_dataset(text, name, variants, strategy)
        get_analytics_store().record(
            "trojan_generated",
            circuit_name=result["circuit"],
            variants=result["variants_generated"],
            strategy=result["strategy"],
        )

        output_dir = result["output_dir"]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(output_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.join(name, os.path.relpath(fpath, output_dir))
                    zf.write(fpath, arcname)
        buffer.seek(0)

        headers = {
            "Content-Disposition": f'attachment; filename="{name}_trojan_dataset.zip"',
            "X-Circuit-Name": result["circuit"],
            "X-Variants-Generated": str(result["variants_generated"]),
            "X-Strategy": result["strategy"],
        }
        return StreamingResponse(buffer, media_type="application/zip", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
