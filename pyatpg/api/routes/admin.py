"""Admin endpoints: see what is occupying the server and clear the queue.

Disabled unless the server has an admin token configured
(``PYATPG_ADMIN_TOKEN``); every request must then send it in the
``X-Admin-Token`` header. Without that, anyone who can reach the public API
could cancel other people's analyses.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from pyatpg.api.concurrency import slot_count
from pyatpg.core import progress

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = os.environ.get("PYATPG_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin features are disabled on this server: set the PYATPG_ADMIN_TOKEN "
                   "environment variable to enable them.",
        )
    if not hmac.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


@router.get("/runs", dependencies=[Depends(require_admin)])
def list_runs() -> Dict[str, Any]:
    """Every analysis that is running or waiting for a slot."""
    runs = progress.list_runs()
    return {
        "slots": slot_count(),
        "running": [r for r in runs if r["state"] == "running"],
        "queued": [r for r in runs if r["state"] == "queued"],
    }


class CancelRequest(BaseModel):
    #: One run to cancel; omitted = cancel every queued and running run.
    id: Optional[str] = None


@router.post("/runs/cancel", dependencies=[Depends(require_admin)])
def cancel_runs(req: CancelRequest) -> Dict[str, Any]:
    """Cancel one run, or clear the whole queue (running runs included).

    Cancelled runs stop at their next checkpoint -- normally within a second
    or two -- and free their slot.
    """
    if req.id:
        if not progress.cancel(req.id):
            raise HTTPException(status_code=404, detail="No such run.")
        return {"cancelled": 1}
    return {"cancelled": progress.cancel_all()}
