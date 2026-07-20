import hashlib
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/v1/governance", tags=["Governance Layer"])

class RetrievalCheckRequest(BaseModel):
    agent_id: str
    provider: str  # e.g., "parallel", "perplexity", "internal-crawler"
    target_domain: str
    intent: str
    requires_paywall_bypass: bool = False
    requires_antibot_bypass: bool = False

class RetrievalCheckResponse(BaseModel):
    allowed: bool
    reason: str
    policy_mode: str
    provider_licensed: bool
    robots_compliant: bool
    evidence_id: Optional[str] = None
    telemetry: Dict[str, Any]

@router.post("/check-retrieval", response_model=RetrievalCheckResponse)
async def check_retrieval(request: RetrievalCheckRequest):
    # Dummy policy check logic for the PiP Terminal
    
    # 1. Block high-risk providers without explicit license
    provider_licensed = False
    if request.provider.lower() in ["parallel", "perplexity"]:
        # Simulate that "Parallel" is an unlicensed high-risk pipe unless configured
        if request.requires_antibot_bypass:
            return RetrievalCheckResponse(
                allowed=False,
                reason="Anti-bot bypass requested via unlicensed provider (Parallel). Blocked by Clean Web Policy.",
                policy_mode="clean_web",
                provider_licensed=False,
                robots_compliant=False,
                telemetry={"risk_score": 95}
            )
        
    # 2. Enforce robots.txt policy
    robots_compliant = not request.requires_antibot_bypass
    
    # 3. Simulate success for safe requests
    evidence_id = f"ev_{hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]}"
    
    return RetrievalCheckResponse(
        allowed=True,
        reason="Retrieval access permitted under current policy.",
        policy_mode="licensed_retrieval" if provider_licensed else "research_mode",
        provider_licensed=provider_licensed,
        robots_compliant=robots_compliant,
        evidence_id=evidence_id,
        telemetry={
            "risk_score": 10 if robots_compliant else 50,
            "latency_ms": 12,
            "governed_by": "Veklom-CAPI"
        }
    )
