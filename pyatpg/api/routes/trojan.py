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
    seed: int | None = Form(None),
    verify_activation: bool = Form(False),
):
    """Generate a trojan dataset from an uploaded circuit file and return it as a zip.

    `verify_activation` runs one extra targeted Atalanta job per insertion to
    confirm the trigger is actually reachable and observable, dropping any
    variant that isn't -- requires a working Atalanta binary on this
    deployment and roughly doubles the work per variant.
    """
    try:
        content = await file.read()
        text = content.decode("utf-8")
        # The stem names the dataset; the full filename is passed separately
        # because its extension is what selects the parser.
        filename = file.filename or "circuit.v"
        name = filename.rsplit('.', 1)[0]
        result = TrojanService.generate_dataset(
            text, name, variants, strategy,
            filename=filename, seed=seed, verify_activation=verify_activation,
        )
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
            "X-Variants-Requested": str(result["variants_requested"]),
            "X-Strategy": result["strategy"],
        }
        return StreamingResponse(buffer, media_type="application/zip", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
