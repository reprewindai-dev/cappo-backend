from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from cappo_backend.api.routers.execution_projection_router import _project
from cappo_backend.execution.budget_ledger import BudgetLedger, SettlementConflict
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.models.workspace_budget import HoldStatus, WorkspaceBudget, WorkspaceBudgetHold
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def test_local_settlement_is_exactly_once(db):
    workspace_id = _id("ws")
    execution_id = _id("exec")
    db.add(WorkspaceBudget(workspace_id=workspace_id, balance_cents=100))
    db.commit()

    ledger = BudgetLedger(db)
    ledger.reserve(execution_id=execution_id, workspace_id=workspace_id, amount_cents=10)
    db.commit()
    first = ledger.settle_local(
        execution_id=execution_id,
        evidence_hash="evidence-1",
        endpoint="sandbox_file_append",
    )
    db.commit()
    second = ledger.settle_local(
        execution_id=execution_id,
        evidence_hash="evidence-1",
        endpoint="sandbox_file_append",
    )
    db.commit()

    assert first.already_settled is False
    assert second.already_settled is True
    assert first.ledger_id == second.ledger_id
    assert db.get(WorkspaceBudget, workspace_id).balance_cents == 90
    assert db.get(WorkspaceBudgetHold, execution_id).status == HoldStatus.SETTLED
    rows = db.execute(
        select(X402ConsumedPayment).where(X402ConsumedPayment.execution_id == execution_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].chain_id == "local-ledger"


def test_settled_hold_cannot_be_released(db):
    workspace_id = _id("ws")
    execution_id = _id("exec")
    db.add(WorkspaceBudget(workspace_id=workspace_id, balance_cents=100))
    db.commit()
    ledger = BudgetLedger(db)
    ledger.reserve(execution_id=execution_id, workspace_id=workspace_id, amount_cents=10)
    db.commit()
    ledger.settle_local(
        execution_id=execution_id,
        evidence_hash="evidence-2",
        endpoint="sandbox_file_append",
    )
    db.commit()

    with pytest.raises(SettlementConflict):
        ledger.release(execution_id=execution_id)


def test_projection_is_workspace_scoped(db):
    workspace_id = _id("ws")
    execution_id = _id("exec")
    db.add(
        ConsequenceExecutionEvent(
            event_id=_id("evt"),
            operation_id=execution_id,
            intent_hash="intent-hash",
            state="succeeded",
            version=0,
            receipt_id="lease-test",
            mount_id="n8n-17-sandbox-file-append",
            execution_id=execution_id,
            principal=workspace_id,
            action="fs:append",
            resource="sandbox:n8n-governed-append",
            completion_proof_type="reconciliation_filesystem",
            completion_proof_ref="record-hash",
            proof_subject_hash="proof-hash",
        )
    )
    db.commit()

    projection = _project(db, execution_id, workspace_id)
    assert projection["status"] == "COMPLETED"
    assert projection["evidence_hash"] == "record-hash"
    assert projection["can_cancel"] is False
    assert projection["onchain_x402_verified"] is False

    with pytest.raises(Exception) as denied:
        _project(db, execution_id, "different-workspace")
    assert getattr(denied.value, "status_code", None) == 404
