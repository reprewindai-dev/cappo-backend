"""VNP Control Plane Router — provider and API onboarding.

Exposes endpoints for managing providers, APIs, and credentials.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.services.vnp_control_plane import VNPControlPlaneService

router = APIRouter(prefix="/v1/vnp/admin", tags=["VNP Control Plane"])


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    commercial_profile: dict[str, Any] | None = Field(default=None, max_length=32)


class APIRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    endpoint: str = Field(min_length=1, max_length=2048)
    version: str = Field(default="v1.0.0", min_length=1, max_length=50)
    x402Ready: bool = False


@router.post("/providers")
async def register_provider(
    request: ProviderRequest,
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    service = VNPControlPlaneService(db)
    provider = service.register_provider(request.name, request.commercial_profile)
    return {
        "id": str(provider.id),
        "name": provider.name,
        "did": provider.did
    }


@router.post("/providers/{provider_id}/apis")
async def register_provider_api(
    provider_id: uuid.UUID,
    request: APIRegistrationRequest,
    db: Session = Depends(get_session)
) -> dict[str, Any]:
    service = VNPControlPlaneService(db)
    api = service.register_api(
        provider_id=provider_id,
        name=request.name,
        endpoint=request.endpoint,
        version=request.version,
        x402_compliant=request.x402Ready
    )
    return {
        "id": str(api.id),
        "api_did": api.api_did,
        "name": api.name
    }
