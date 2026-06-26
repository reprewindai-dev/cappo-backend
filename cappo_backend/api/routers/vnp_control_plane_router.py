"""VNP Control Plane Router — provider and API onboarding.

Exposes endpoints for managing providers, APIs, and credentials.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.services.vnp_control_plane import VNPControlPlaneService

router = APIRouter(prefix="/v1/vnp/admin", tags=["VNP Control Plane"])


@router.post("/providers")
async def register_provider(
    request: dict[str, Any],
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    service = VNPControlPlaneService(db)
    name = request.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing provider name")

    provider = service.register_provider(name, request.get("commercial_profile"))
    return {
        "id": str(provider.id),
        "name": provider.name,
        "did": provider.did
    }


@router.post("/providers/{provider_id}/apis")
async def register_provider_api(
    provider_id: uuid.UUID,
    request: dict[str, Any],
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    service = VNPControlPlaneService(db)
    name = request.get("name")
    endpoint = request.get("endpoint")
    if not name or not endpoint:
        raise HTTPException(status_code=400, detail="Missing API name or endpoint")

    api = service.register_api(
        provider_id=provider_id,
        name=name,
        endpoint=endpoint,
        version=request.get("version", "v1.0.0"),
        x402_compliant=request.get("x402Ready", False)
    )
    return {
        "id": str(api.id),
        "api_did": api.api_did,
        "name": api.name
    }
