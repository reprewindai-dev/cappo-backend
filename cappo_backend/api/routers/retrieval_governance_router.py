from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1/governance", tags=["Governance Layer"])


class RetrievalCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    target_domain: str = Field(min_length=1, max_length=253)
    intent: str = Field(min_length=1, max_length=4096)
    requires_paywall_bypass: bool = False
    requires_antibot_bypass: bool = False


class RetrievalCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["blocked", "unmeasured"]
    allowed: bool
    reason: str
    policy_mode: str
    provider_licensed: bool | None = None
    robots_compliant: bool | None = None
    evidence_id: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)


@router.post("/check-retrieval", response_model=RetrievalCheckResponse)
async def check_retrieval(request: RetrievalCheckRequest):
    """Return a measured policy decision; absent licensing data is not permission."""
    if request.requires_paywall_bypass or request.requires_antibot_bypass:
        return RetrievalCheckResponse(
            state="blocked",
            allowed=False,
            reason="Bypass behavior is prohibited by Clean Web Policy.",
            policy_mode="clean_web",
            robots_compliant=False,
            telemetry={"governed_by": "Veklom-CAPI"},
        )

    # Provider licensing and robots compliance require authoritative dependencies.
    # Do not turn absent measurements into an allow decision or fake evidence.
    return RetrievalCheckResponse(
        state="unmeasured",
        allowed=False,
        reason="Provider licensing and robots compliance could not be measured; retrieval is blocked.",
        policy_mode="fail_closed",
        telemetry={"governed_by": "Veklom-CAPI"},
    )
