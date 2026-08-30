from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
import uuid
import logging

from cappo_backend.db.session import SessionLocal
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent

router = APIRouter(prefix="/api/v1/reconcile", tags=["reconciliation"])
logger = logging.getLogger(__name__)

@router.post("/{execution_id}")
async def reconcile_execution(execution_id: str):
    with SessionLocal() as db:
        events = db.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.execution_id == execution_id)
            .order_by(ConsequenceExecutionEvent.version.asc())
        ).scalars().all()
        
        if not events:
            raise HTTPException(status_code=404, detail="Execution not found")
            
        latest = events[-1]
        if latest.state not in ("outcome_unknown", "started"):
            return {"status": "skipped", "reason": f"Execution is in terminal or non-reconcilable state: {latest.state}"}
            
        connector_url = f"http://127.0.0.1:8099/connectors/sandbox-file-append/status/{execution_id}"
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(connector_url, timeout=5.0)
                if resp.status_code == 404:
                    return {"status": "pending", "reason": "Target has no evidence of this execution yet"}
                resp.raise_for_status()
                evidence = resp.json()
        except Exception as e:
            logger.error(f"Reconciliation failed to contact connector: {e}")
            return {"status": "failed", "reason": str(e)}

        op_id = latest.operation_id
        new_version = latest.version + 1
        
        ce_recon = ConsequenceExecutionEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            operation_id=op_id,
            intent_hash=latest.intent_hash,
            state="reconciled_succeeded",
            version=new_version,
            mount_id=latest.mount_id,
            execution_id=execution_id,
            principal=latest.principal,
            action=latest.action,
            resource=latest.resource,
            completion_proof_type="reconciliation_api_query",
            proof_subject_hash=evidence.get("receipt", {}).get("operation_id", "mock_hash")
        )
        
        db.add(ce_recon)
        db.commit()
        
        return {"status": "reconciled_succeeded", "evidence": evidence}

__all__ = ["router"]
