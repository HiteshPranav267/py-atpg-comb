"""Discovery endpoint for the LLM providers the backend can talk to.

Lets the frontend build its provider picker from the same registry the
backend resolves requests against, so adding a provider in
``pyatpg.ranking.providers`` is enough to surface it in the UI.
"""
from typing import Any, Dict, List

from fastapi import APIRouter

from pyatpg.ranking.providers import (
    AUTO_PROVIDER,
    DEFAULT_PROVIDER,
    list_provider_specs,
    resolve_llm_config,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/providers")
def get_providers() -> Dict[str, Any]:
    """List supported providers and which one the server is configured for.

    ``server_default`` is the provider the backend would use for a request
    that supplies no key of its own (or null when none is configured);
    the credential itself is never returned.
    """
    server_default = None
    try:
        config = resolve_llm_config()
    except ValueError:
        config = None
    if config is not None:
        server_default = {"provider": config.provider, "model": config.model}
    providers: List[Dict[str, Any]] = list_provider_specs()
    return {
        "auto": AUTO_PROVIDER,
        "default_provider": DEFAULT_PROVIDER,
        "providers": providers,
        "server_default": server_default,
    }
