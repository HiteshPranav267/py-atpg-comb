from typing import Any, Dict

from fastapi import APIRouter, Query

from pyatpg.core.analytics_store import get_analytics_store

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
def get_summary(recent_limit: int = Query(25, ge=1, le=200)) -> Dict[str, Any]:
    """In-app product-usage analytics: circuits parsed, ATPG runs (by kind,
    with fault coverage and LLM usage), ranking/benchmark/Trojan-generation
    counts, and recent activity.

    This is not visitor/page-view analytics -- that's tracked separately by
    Vercel Web Analytics on the frontend, and isn't queryable from here.
    """
    return get_analytics_store().summary(recent_limit=recent_limit)
