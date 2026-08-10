"""CAPPO protocol discovery endpoints."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cappo_backend.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["protocol"])

MANIFEST: dict[str, Any] = {
    "service": "cappo",
    "repo": "reprewindai-dev/cappo-backend",
    "role": "governed-execution-control-plane",
    "version": "0.1.0",
    "base_url": "https://cappo.veklom.com",
    "health": "/health",
    "dependencies": "/health/dependencies",
    "auth_mode": "api-key",
    "status": "ok",
    "capabilities": ["authorize_execution", "governed_execute"],
    "links": {
        "cappo": "https://cappo.veklom.com/protocol.json",
        "capi": "https://capi.veklom.com/protocol.json",
        "pgl": "https://pgl.veklom.com/protocol.json",
        "byos": "https://api.veklom.com/protocol.json",
    },
}


class IntrospectQuery(BaseModel):
    query: str


@router.get("/protocol.json", include_in_schema=False)
def get_protocol_manifest() -> dict[str, Any]:
    return MANIFEST


@router.post("/protocol/introspect", include_in_schema=False)
async def introspect_capabilities(
    body: IntrospectQuery,
    settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    query = body.query.lower()
    
    # Start with base capabilities
    capabilities = list(MANIFEST["capabilities"])
    
    # Dynamically query cAPI for registered federation capabilities
    if settings.capi_backend_url:
        url = f"{settings.capi_backend_url.rstrip('/')}/api/v1/registry/services"
        try:
            async with httpx.AsyncClient() as client:
                headers = {}
                if settings.capi_api_key:
                    headers["Authorization"] = f"Bearer {settings.capi_api_key}"
                
                response = await client.get(url, headers=headers, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    for svc in data.get("services", []):
                        for cap in svc.get("capabilities", []):
                            if cap not in capabilities:
                                capabilities.append(cap)
                else:
                    logger.warning(f"Failed to fetch capabilities from cAPI: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"Error fetching capabilities from cAPI: {e}")

    matches = [capability for capability in capabilities if query == "*" or query in capability]
    
    return {
        "query": body.query,
        "matches": matches,
        "total": len(matches),
        "auth_mode": MANIFEST["auth_mode"],
        "links": MANIFEST["links"],
    }
