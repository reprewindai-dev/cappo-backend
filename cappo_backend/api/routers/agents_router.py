"""FastAPI Router for PGL Agents and Ledger Events (/api/v1)."""

from __future__ import annotations

import datetime
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cappo_backend.db.session import get_session
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent

router = APIRouter(prefix="/api/v1", tags=["Agents"])


# ---------- schemas ----------

class AgentCreateRequest(BaseModel):
    agent_name: str
    creator: str
    jurisdiction: str = "US"
    genome: Dict[str, Any]
    parent_agent_ids: List[str] = Field(default_factory=list)


class LedgerEventCreateRequest(BaseModel):
    agent_id: str
    event_type: str
    actor: str = "veklom-system"
    summary: str = "Agent execution event"
    details: Dict[str, Any]


# ---------- helper functions ----------

def canonical_hash(obj: dict) -> str:
    """Compute SHA-256 over canonical JSON representation."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------- routes ----------

@router.post("/agents", response_model=Dict[str, Any])
def register_agent(
    body: AgentCreateRequest,
    db: Session = Depends(get_session),
) -> Any:
    """Register a new agent with GnomLedger and mint birth certificate."""
    try:
        agent_id = f"agent_{uuid.uuid4().hex[:16]}"
        certificate_id = f"cert_ubc_{uuid.uuid4().hex[:16]}"

        genome_payload = body.genome
        genome_hash_val = canonical_hash(genome_payload)
        
        constitution_data = {
            "tools": genome_payload.get("tools", ["governance", "policy-check"]),
            "permissions": genome_payload.get("permissions", ["read"]),
            "safety_rules": genome_payload.get("safety_rules", ["no_secrets"]),
        }
        constitution_hash_val = canonical_hash(constitution_data)

        # Create PGLCertificate entry
        cert = PGLCertificate(
            certificate_id=certificate_id,
            run_id="run_genesis",
            workspace_id=genome_payload.get("workspace_id") or body.creator,
            actor_id=body.creator,
            agent_id=agent_id,
            genome_hash=genome_hash_val,
            constitution_hash=constitution_hash_val,
            plan_hash="plan_genesis",
            governance_decision="approved",
            risk_tier=genome_payload.get("risk_category", "low"),
            approved_budget_cents=0,
            reserve_cents=0,
            provenance_json={
                "agent_name": body.agent_name,
                "creator": body.creator,
                "jurisdiction": body.jurisdiction,
                "genome": genome_payload,
                "parent_agent_ids": body.parent_agent_ids,
            },
            persisted=True,
        )
        db.add(cert)

        # Create initial PGLLedgerEvent entry
        event_payload = {
            "certificate_id": certificate_id,
            "agent_id": agent_id,
            "genome_hash": genome_hash_val,
            "constitution_hash": constitution_hash_val,
            "agent_name": body.agent_name,
            "creator": body.creator,
            "jurisdiction": body.jurisdiction,
            "genome": genome_payload,
            "parent_agent_ids": body.parent_agent_ids,
        }
        event_hash_val = canonical_hash(event_payload)

        event = PGLLedgerEvent(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            certificate_id=certificate_id,
            event_type="birth_registration",
            payload=event_payload,
            previous_event_hash=None,
            event_hash=event_hash_val,
        )
        db.add(event)
        
        db.commit()
        db.refresh(cert)
        db.refresh(event)

        return {
            "certificate_id": certificate_id,
            "agent_id": agent_id,
            "name": body.agent_name,
            "creator": body.creator,
            "jurisdiction": body.jurisdiction,
            "status": "active",
            "genome_hash": genome_hash_val,
            "genome": genome_payload,
            "parent_agent_ids": body.parent_agent_ids,
            "created_at": cert.created_at.isoformat() if cert.created_at else datetime.now(timezone.utc).isoformat(),
            "version_count": 1,
            "ledger_events": [
                {
                    "event_id": event.event_id,
                    "certificate_id": event.certificate_id,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "previous_event_hash": event.previous_event_hash,
                    "event_hash": event.event_hash,
                    "created_at": event.created_at.isoformat() if event.created_at else datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agents", response_model=List[Dict[str, Any]])
def list_agents(
    limit: int = 100,
    cursor: str | None = None,
    db: Session = Depends(get_session),
) -> Any:
    """List all registered agents."""
    try:
        query = db.query(PGLCertificate).filter(PGLCertificate.agent_id.isnot(None))
        certs = query.limit(limit).all()
        
        results = []
        for cert in certs:
            events = db.query(PGLLedgerEvent).filter(
                PGLLedgerEvent.certificate_id == cert.certificate_id
            ).order_by(PGLLedgerEvent.created_at.asc()).all()
            
            prov = cert.provenance_json or {}
            results.append({
                "certificate_id": cert.certificate_id,
                "agent_id": cert.agent_id,
                "name": prov.get("agent_name") or prov.get("name") or "Agent",
                "creator": cert.actor_id,
                "jurisdiction": prov.get("jurisdiction") or "US",
                "status": "active",
                "genome_hash": cert.genome_hash,
                "genome": prov.get("genome") or {},
                "parent_agent_ids": prov.get("parent_agent_ids") or [],
                "created_at": cert.created_at.isoformat() if cert.created_at else datetime.now(timezone.utc).isoformat(),
                "version_count": len(events),
                "ledger_events": [
                    {
                        "event_id": e.event_id,
                        "certificate_id": e.certificate_id,
                        "event_type": e.event_type,
                        "payload": e.payload,
                        "previous_event_hash": e.previous_event_hash,
                        "event_hash": e.event_hash,
                        "created_at": e.created_at.isoformat() if e.created_at else datetime.now(timezone.utc).isoformat(),
                    }
                    for e in events
                ],
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agents/{agent_id}", response_model=Dict[str, Any])
def get_agent(
    agent_id: str,
    db: Session = Depends(get_session),
) -> Any:
    """Get agent details by agent_id or certificate_id."""
    cert = db.query(PGLCertificate).filter(
        (PGLCertificate.agent_id == agent_id) | (PGLCertificate.certificate_id == agent_id)
    ).first()
    
    if not cert:
        raise HTTPException(status_code=404, detail="Agent not found")
        
    events = db.query(PGLLedgerEvent).filter(
        PGLLedgerEvent.certificate_id == cert.certificate_id
    ).order_by(PGLLedgerEvent.created_at.asc()).all()
    
    prov = cert.provenance_json or {}
    return {
        "certificate_id": cert.certificate_id,
        "agent_id": cert.agent_id,
        "name": prov.get("agent_name") or prov.get("name") or "Agent",
        "creator": cert.actor_id,
        "jurisdiction": prov.get("jurisdiction") or "US",
        "status": "active",
        "genome_hash": cert.genome_hash,
        "genome": prov.get("genome") or {},
        "parent_agent_ids": prov.get("parent_agent_ids") or [],
        "created_at": cert.created_at.isoformat() if cert.created_at else datetime.now(timezone.utc).isoformat(),
        "version_count": len(events),
        "ledger_events": [
            {
                "event_id": e.event_id,
                "certificate_id": e.certificate_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "previous_event_hash": e.previous_event_hash,
                "event_hash": e.event_hash,
                "created_at": e.created_at.isoformat() if e.created_at else datetime.now(timezone.utc).isoformat(),
            }
            for e in events
        ],
    }


@router.get("/agents/{agent_id}/certificate", response_model=Dict[str, Any])
def get_agent_certificate(
    agent_id: str,
    db: Session = Depends(get_session),
) -> Any:
    """Get agent certificate alias endpoint."""
    return get_agent(agent_id=agent_id, db=db)


@router.post("/ledger/events", response_model=Dict[str, Any])
def create_ledger_event(
    body: LedgerEventCreateRequest,
    db: Session = Depends(get_session),
) -> Any:
    """Create a new ledger event and append to the agent's hash chain."""
    try:
        cert = db.query(PGLCertificate).filter(
            (PGLCertificate.agent_id == body.agent_id) | (PGLCertificate.certificate_id == body.agent_id)
        ).first()
        
        if not cert:
            raise HTTPException(status_code=404, detail="Agent certificate not found")
            
        last_event = db.query(PGLLedgerEvent).filter(
            PGLLedgerEvent.certificate_id == cert.certificate_id
        ).order_by(PGLLedgerEvent.created_at.desc()).first()
        
        prev_hash = last_event.event_hash if last_event else None
        
        # Build payload
        event_payload = {
            "agent_id": body.agent_id,
            "event_type": body.event_type,
            "actor": body.actor,
            "summary": body.summary,
            "details": body.details,
            "previous_event_hash": prev_hash,
        }
        
        # Chain: SHA-256(canonical_payload + prev_hash)
        chain_input = json.dumps(event_payload, sort_keys=True, separators=(",", ":"))
        if prev_hash:
            chain_input += prev_hash
        event_hash_val = hashlib.sha256(chain_input.encode()).hexdigest()
        
        event = PGLLedgerEvent(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            certificate_id=cert.certificate_id,
            event_type=body.event_type,
            payload=event_payload,
            previous_event_hash=prev_hash,
            event_hash=event_hash_val,
        )
        
        db.add(event)
        db.commit()
        db.refresh(event)
        
        return {
            "event_id": event.event_id,
            "certificate_id": event.certificate_id,
            "event_type": event.event_type,
            "payload": event.payload,
            "previous_event_hash": event.previous_event_hash,
            "event_hash": event.event_hash,
            "created_at": event.created_at.isoformat() if event.created_at else datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ledger/agents/{agent_id}", response_model=List[Dict[str, Any]])
def get_agent_history(
    agent_id: str,
    limit: int = 200,
    db: Session = Depends(get_session),
) -> Any:
    """Fetch the full ledger history in order of creation."""
    try:
        cert = db.query(PGLCertificate).filter(
            (PGLCertificate.agent_id == agent_id) | (PGLCertificate.certificate_id == agent_id)
        ).first()
        
        if not cert:
            raise HTTPException(status_code=404, detail="Agent not found")
            
        events = db.query(PGLLedgerEvent).filter(
            PGLLedgerEvent.certificate_id == cert.certificate_id
        ).order_by(PGLLedgerEvent.created_at.asc()).limit(limit).all()
        
        return [
            {
                "event_id": e.event_id,
                "certificate_id": e.certificate_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "previous_event_hash": e.previous_event_hash,
                "event_hash": e.event_hash,
                "created_at": e.created_at.isoformat() if e.created_at else datetime.now(timezone.utc).isoformat(),
            }
            for e in events
        ]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/ledger/agents/{agent_id}/verify", response_model=Dict[str, Any])
def verify_agent_chain(
    agent_id: str,
    db: Session = Depends(get_session),
) -> Any:
    """Validate the cryptographic integrity of the agent's ledger chain."""
    try:
        cert = db.query(PGLCertificate).filter(
            (PGLCertificate.agent_id == agent_id) | (PGLCertificate.certificate_id == agent_id)
        ).first()
        
        if not cert:
            raise HTTPException(status_code=404, detail="Agent not found")
            
        events = db.query(PGLLedgerEvent).filter(
            PGLLedgerEvent.certificate_id == cert.certificate_id
        ).order_by(PGLLedgerEvent.created_at.asc()).all()
        
        chain_valid = True
        reason = "Chain is valid"
        
        for i, evt in enumerate(events):
            if i == 0:
                expected_prev = None
            else:
                expected_prev = events[i - 1].event_hash
                
            if evt.previous_event_hash != expected_prev:
                chain_valid = False
                reason = f"Hash link broken at event index {i}: expected previous {expected_prev}, got {evt.previous_event_hash}"
                break
                
            # Re-derive the hash
            event_payload = evt.payload
            chain_input = json.dumps(event_payload, sort_keys=True, separators=(",", ":"))
            if evt.previous_event_hash:
                chain_input += evt.previous_event_hash
            derived_hash = hashlib.sha256(chain_input.encode()).hexdigest()
            if derived_hash != evt.event_hash:
                chain_valid = False
                reason = f"Hash mismatch at event index {i}: derived {derived_hash}, stored {evt.event_hash}"
                break
                
        return {
            "valid": chain_valid,
            "reason": reason,
            "event_count": len(events),
            "chain_head": events[-1].event_hash if events else None,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
