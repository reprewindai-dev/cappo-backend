import logging
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from cappo_backend.db.session import SessionLocal
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent

router = APIRouter(prefix="/api/v1/reconcile", tags=["reconciliation"])
logger = logging.getLogger(__name__)


def _connector_base_url(request: Request) -> str:
    """The target connector that holds execution evidence, from settings only.

    The reconciler asserts ``reconciled_succeeded`` on the strength of what this
    host returns, so it must be deployment configuration, never a baked-in
    loopback address. Unset means the reconciler is not operable: 503.
    """
    settings = request.app.state.settings
    base = (getattr(settings, "reconcile_connector_base_url", "") or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "RECONCILE_CONNECTOR_UNCONFIGURED",
                "detail": "RECONCILE_CONNECTOR_BASE_URL is not set; reconciliation is unavailable.",
            },
        )
    return base


@router.post("/{execution_id}")
async def reconcile_execution(execution_id: str, request: Request):
    connector_base = _connector_base_url(request)
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

        connector_url = f"{connector_base}/connectors/sandbox-file-append/status/{execution_id}"

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

        # The proof subject must be the target's own receipt. Without it there is
        # nothing to bind the reconciled state to, so the reconcile fails rather
        # than recording a placeholder hash as evidence.
        receipt = evidence.get("receipt") if isinstance(evidence, dict) else None
        proof_subject = receipt.get("operation_id") if isinstance(receipt, dict) else None
        if not isinstance(proof_subject, str) or not proof_subject.strip():
            logger.error("Reconciliation evidence for %s carries no receipt.operation_id", execution_id)
            return {
                "status": "failed",
                "reason": "Connector evidence has no receipt.operation_id; refusing to assert reconciled_succeeded",
            }

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
            proof_subject_hash=proof_subject,
        )

        db.add(ce_recon)
        db.commit()

        return {"status": "reconciled_succeeded", "evidence": evidence}

__all__ = ["router"]
