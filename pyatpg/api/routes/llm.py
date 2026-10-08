"""Discovery endpoint for the LLM providers the backend can talk to.

Lets the frontend build its provider picker from the same registry the
backend resolves requests against, so adding a provider in
``pyatpg.ranking.providers`` is enough to surface it in the UI.
"""
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pyatpg.ranking.providers import (
    AUTO_PROVIDER,
    DEFAULT_PROVIDER,
    LLMUnavailableError,
    create_provider,
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


class ModelListRequest(BaseModel):
    """Which account to ask. The key is used for this one call and never stored."""

    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None


@router.post("/models")
def list_models(req: ModelListRequest) -> Dict[str, Any]:
    """Models the given credential can use for text generation.

    The AI Studio / vendor dashboards show display names, but a request
    needs the model *id*; this asks the vendor for both, and only for the
    models that can run this project's prompts (Gemini reports which
    support ``generateContent``). With no key in the request the server's
    own configured credential is used, so the picker works either way.
    """
    try:
        config = resolve_llm_config(
            provider=req.provider or None,
            api_key=req.api_key or None,
            base_url=req.base_url or None,
            require_model=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if config is None:
        raise HTTPException(
            status_code=400,
            detail="No API key: paste one, or configure one on the server.",
        )
    try:
        provider = create_provider(config)
        models = provider.list_models()
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NotImplementedError:
        raise HTTPException(
            status_code=400,
            detail=f"{config.spec.label} does not support listing models.",
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=502, detail=f"Could not list models: {exc.reason}.")
    return {
        "provider": config.provider,
        "default_model": config.model,
        "models": models,
    }
