"""Authorization-only API surface."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from cappo_backend.services.authorization import evaluate_authorization

router = APIRouter(prefix="/api/v1/execution", tags=["execution"])


class AuthorizationRequest(BaseModel):
    agent_id: str
    capability_id: str = "exec"
    request: dict[str, Any] = Field(default_factory=dict)
    trust_score: float = Field(default=75.0, ge=0.0, le=100.0)
    requests_per_hour: float = Field(default=0.0, ge=0.0)
    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    time_of_day: int = Field(default=12, ge=0, le=23)
    directive: str | None = None
    risk_tier: str | None = None


class AuthorizationResponse(BaseModel):
    decision: str
    authorization_id: str
    lane: str
    decision_hash: str
    reason: str
    evidence_hash: str


@router.post("/authorize", response_model=AuthorizationResponse)
def authorize_execution(body: AuthorizationRequest) -> AuthorizationResponse:
    """Return a local governance decision without starting execution."""
    result = evaluate_authorization(body.model_dump())
    return AuthorizationResponse(
        **result,
        authorization_id=f"auth_{uuid.uuid4().hex}",
    )


__all__ = ["router"]
