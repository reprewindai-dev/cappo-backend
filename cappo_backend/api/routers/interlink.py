"""Retired public Interlink execution ingress.

Interlink remains a federation/MCP concern, but it cannot expose a parallel
HTTP proxy that can create consequences outside CAPPO. Public execution is
therefore exclusively ``POST /v1/exec``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_interlink(path: str, request: Request) -> None:
    """Return a terminal migration response without reading or forwarding input."""
    del path, request
    raise HTTPException(
        status_code=410,
        detail="Execution is governed exclusively by POST /v1/exec",
    )
