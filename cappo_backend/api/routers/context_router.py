import time
from typing import Any, Dict

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cappo_backend.config import get_settings
from cappo_backend.core.governance.context_shaper import ContextShaper
from cappo_backend.core.governance.jurisdiction import JurisdictionResolver

router = APIRouter(prefix="/v1/context", tags=["context"])

class ShapeContextRequest(BaseModel):
    jurisdiction: str
    capability: str
    context: Dict[str, Any]
    tenant_id: str
    tenant_jwt: str
    execution_id: str

@router.post("/shape")
def shape_context(request: ShapeContextRequest):
    """
    Evaluates the capability request context against defined policies (PII filtering, 
    secret injection), shapes the context, and persists an audit event to PGL.
    """
    resolver = JurisdictionResolver()
    shaper = ContextShaper()
    settings = get_settings()
    
    policy_bundle = resolver.resolve(request.execution_id, request.tenant_id)
    # override jurisdiction for testing purposes
    policy_bundle.jurisdiction = request.jurisdiction
    
    shaped_payload, audit, decision = shaper.shape_context(
        capability_id=request.capability,
        payload=request.context,
        tenant_jwt=request.tenant_jwt,
        policy_bundle=policy_bundle
    )
    
    # Send to Gnomledger
    body = {
        "agent_id": "cappo-system",
        "event_type": "custom",
        "actor": request.tenant_id,
        "summary": f"Context Shaped for {request.capability}",
        "details": audit,
        "idempotency_key": f"live-test-cappo-{int(time.time()*1000)}"
    }
    headers = {}
    if settings.pgl_ledger_api_key:
        headers["X-API-Key"] = settings.pgl_ledger_api_key
    
    url = f"{settings.pgl_ledger_url.rstrip('/')}/api/v1/events"
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=5.0)
        resp.raise_for_status()
        resp_data = resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to log to PGL: {str(e)}")
        
    # Enforce policy decision
    if decision == "FAIL_CLOSED":
        raise HTTPException(status_code=403, detail="Context Shaping Failed: Blocked by Policy")
    elif decision == "ESCALATE":
        raise HTTPException(status_code=403, detail="Context Shaping Escalated: Requires Human Approval")
        
    return {
        "shaped_payload": shaped_payload,
        "audit": audit,
        "audit_evidence_hash": resp_data.get("event_hash")
    }
