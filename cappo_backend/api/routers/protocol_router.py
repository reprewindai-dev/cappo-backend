"""CAPPO protocol discovery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

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
def introspect_capabilities(body: IntrospectQuery) -> dict[str, Any]:
    query = body.query.lower()
    capabilities = MANIFEST["capabilities"]
    matches = [capability for capability in capabilities if query == "*" or query in capability]
    return {
        "query": body.query,
        "matches": matches,
        "total": len(matches),
        "auth_mode": MANIFEST["auth_mode"],
        "links": MANIFEST["links"],
    }
