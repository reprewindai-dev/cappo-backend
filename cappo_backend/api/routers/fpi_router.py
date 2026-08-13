from fastapi import APIRouter, Header, HTTPException, Request, Depends
from typing import Optional, List, Dict
import uuid
import time
import hashlib

router = APIRouter(prefix="/api/fpi", tags=["Federation Provider Interface"])

# Mock database for simulation
providers_db = {}
leases_db = {}
billing_db = {}

@router.post("/providers/register", status_code=201)
async def register_provider(payload: dict):
    """
    Protocol Module A: Provider Registration & Onboarding
    """
    provider_id = f"prov_{uuid.uuid4().hex[:8]}"
    providers_db[provider_id] = {
        "id": provider_id,
        "base_uri": payload.get("base_uri"),
        "capabilities": payload.get("capabilities", []),
        "status": "active",
        "last_heartbeat": time.time()
    }
    return {"provider_id": provider_id, "status": "registered"}

@router.post("/providers/{provider_id}/status")
async def provider_heartbeat(provider_id: str, payload: dict):
    if provider_id not in providers_db:
        raise HTTPException(status_code=404, detail="Provider not found")
    providers_db[provider_id]["last_heartbeat"] = time.time()
    return {"status": "ok"}

@router.post("/discovery")
async def discovery_matchmaker(payload: dict):
    """
    Protocol Module B: Service Discovery Matchmaker
    """
    cap = payload.get("capability")
    matched = []
    for pid, p in providers_db.items():
        if cap in p["capabilities"]:
            matched.append(p)
    return {"providers": matched}

@router.post("/resources/allocate")
async def allocate_resources(payload: dict):
    """
    Protocol Module C: Resource Quota & Lease Allocation
    """
    lease_id = f"lease_{uuid.uuid4().hex[:12]}"
    f_max = int(time.time() * 1000) # Monotonic fencing token
    leases_db[lease_id] = {
        "lease_id": lease_id,
        "f_max": f_max,
        "provider_id": payload.get("provider_id"),
        "units": payload.get("compute_units")
    }
    return {"lease_id": lease_id, "f_max": f_max, "x402_gas_required": 10}

@router.post("/execute")
async def federated_execute(
    request: Request,
    payload: dict,
    authorization: Optional[str] = Header(None),
    if_match: Optional[str] = Header(None)
):
    """
    Protocol Module D: Federated Execution & Two Invariants Gateway
    """
    # VEK-HTTP Invariant 1: Terminal Authority (Fail-Closed)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=403, 
            detail="CAPPO Authority Denied: Missing or invalid grant token."
        )

    # VEK-HTTP Invariant 2: Fencing Token Requirement
    if not if_match:
        raise HTTPException(
            status_code=428,
            detail="Precondition Required: Must supply If-Match fencing token."
        )
    
    lease_id = payload.get("lease_id")
    lease = leases_db.get(lease_id)
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")

    client_f_c = int(if_match.strip('"'))
    
    # State-Bound Authority Evaluation
    if client_f_c < lease["f_max"]:
        raise HTTPException(
            status_code=412,
            detail=f"Precondition Failed: Stale token (f_c: {client_f_c} < F_max: {lease['f_max']}). Split-brain protection active."
        )

    # VEK-HTTP Infrastructure Failure Simulation
    if payload.get("simulate_provider_failure"):
        raise HTTPException(
            status_code=503,
            detail="Service Unavailable: Target provider offline. Trigger HRMR fallback."
        )

    # Metering for Billing
    provider_id = lease["provider_id"]
    if provider_id not in billing_db:
        billing_db[provider_id] = 0.0
    billing_db[provider_id] += 0.5 # Add micro-gas

    # Generate PGL Receipt
    receipt = hashlib.sha256(f"{lease_id}{time.time()}".encode()).hexdigest()

    return {
        "status": "executed",
        "pgl_receipt": receipt,
        "provider": provider_id
    }

@router.get("/billing")
async def get_billing_ledger():
    """
    Protocol Module E: Billing Ledger & Payout Settlement
    """
    return {"ledger": billing_db}
