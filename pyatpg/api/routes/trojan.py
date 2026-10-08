import io
import os
import zipfile

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

from pyatpg.api.concurrency import run_heavy
from pyatpg.core.rtl import decode_upload
from fastapi.responses import StreamingResponse

from pyatpg.api.services.trojan_service import TrojanService
from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/trojan", tags=["trojan"])


_VALID_GATE_TYPES = {"AND", "OR", "NAND", "NOR", "XOR"}


@router.post("/generate")
async def generate_dataset(
    file: UploadFile = File(...),
    variants: int = Form(10),
    strategy: str = Form("random"),
    seed: int | None = Form(None),
    verify_activation: bool = Form(False),
    num_trigger_inputs: int = Form(2),
    trigger_type: str = Form("AND"),
    payload_type: str = Form("XOR"),
):
    """Generate a trojan dataset from an uploaded circuit file and return it as a zip.

    `verify_activation` runs one extra targeted Atalanta job per insertion to
    confirm the trigger is actually reachable and observable, dropping any
    variant that isn't -- requires a working Atalanta binary on this
    deployment and roughly doubles the work per variant.

    `num_trigger_inputs` sets how many nets the trigger gate combines; higher
    means a rarer (stealthier, harder-to-test) activation condition.
    `trigger_type`/`payload_type` pick the gate used for each, from AND, OR,
    NAND, NOR, XOR.
    """
    if num_trigger_inputs < 1:
        raise HTTPException(status_code=400, detail="num_trigger_inputs must be at least 1")
    if trigger_type.upper() not in _VALID_GATE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"trigger_type must be one of {sorted(_VALID_GATE_TYPES)}",
        )
    if payload_type.upper() not in _VALID_GATE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"payload_type must be one of {sorted(_VALID_GATE_TYPES)}",
        )
    try:
        content = await file.read()
        # The stem names the dataset; the full filename is passed separately
        # because its extension is what selects the parser. A zip of RTL
        # sources arrives here as one bundle named "<archive>.v".
        text, filename = decode_upload(file.filename or "circuit.v", content)
        name = filename.rsplit('.', 1)[0]
        result = await run_heavy(
            TrojanService.generate_dataset,
            text, name, variants, strategy,
            filename=filename, seed=seed, verify_activation=verify_activation,
            num_trigger_inputs=num_trigger_inputs,
            trigger_type=trigger_type, payload_type=payload_type,
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
            "X-Trigger-Inputs": str(result["num_trigger_inputs"]),
            "X-Trigger-Type": result["trigger_type"],
            "X-Payload-Type": result["payload_type"],
        }
        return StreamingResponse(buffer, media_type="application/zip", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
