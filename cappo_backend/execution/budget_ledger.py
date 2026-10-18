"""Transactional budget holds and exactly-once local settlement.

This module records *local ledger* settlement only.  It does not claim that an
on-chain x402 transfer occurred.  A caller may persist an independently
verified chain transaction separately once cryptographic settlement proof is
available.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cappo_backend.models.workspace_budget import (
    HoldStatus,
    WorkspaceBudget,
    WorkspaceBudgetHold,
)
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment
from cappo_backend.services.audit_service import AuditService


class BudgetLedgerError(RuntimeError):
    """Base class for fail-closed budget ledger errors."""


class InsufficientBudget(BudgetLedgerError):
    """Raised when spendable balance cannot cover a new hold."""


class SettlementConflict(BudgetLedgerError):
    """Raised when an execution is reused with inconsistent settlement data."""


@dataclass(frozen=True)
class SettlementResult:
    execution_id: str
    amount_cents: int
    ledger_id: str
    audit_hash: str
    already_settled: bool


class BudgetLedger:
    """PostgreSQL-safe budget reservation and local settlement service."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def reserve(self, *, execution_id: str, workspace_id: str, amount_cents: int) -> WorkspaceBudgetHold:
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")

        budget = self.db.execute(
            select(WorkspaceBudget)
            .where(WorkspaceBudget.workspace_id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if budget is None:
            raise BudgetLedgerError("workspace budget is not configured")

        existing = self.db.get(WorkspaceBudgetHold, execution_id)
        if existing is not None:
            if existing.workspace_id != workspace_id or existing.amount_cents != amount_cents:
                raise SettlementConflict("execution_id already has a different budget hold")
            return existing

        active_holds = self.db.execute(
            select(func.coalesce(func.sum(WorkspaceBudgetHold.amount_cents), 0)).where(
                WorkspaceBudgetHold.workspace_id == workspace_id,
                WorkspaceBudgetHold.status == HoldStatus.ACTIVE,
            )
        ).scalar_one()
        if budget.balance_cents - int(active_holds) < amount_cents:
            raise InsufficientBudget("insufficient spendable workspace balance")

        hold = WorkspaceBudgetHold(
            execution_id=execution_id,
            workspace_id=workspace_id,
            amount_cents=amount_cents,
            status=HoldStatus.ACTIVE,
        )
        self.db.add(hold)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise SettlementConflict("concurrent hold collision") from exc
        return hold

    def settle_local(
        self,
        *,
        execution_id: str,
        evidence_hash: str,
        endpoint: str,
    ) -> SettlementResult:
        """Settle one active hold exactly once and append hash-chained evidence."""
        hold = self.db.execute(
            select(WorkspaceBudgetHold)
            .where(WorkspaceBudgetHold.execution_id == execution_id)
            .with_for_update()
        ).scalar_one_or_none()
        if hold is None:
            raise BudgetLedgerError("budget hold does not exist")
        if hold.status == HoldStatus.RELEASED:
            raise SettlementConflict("released hold cannot be settled")

        existing = self.db.execute(
            select(X402ConsumedPayment).where(X402ConsumedPayment.execution_id == execution_id)
        ).scalar_one_or_none()
        if existing is not None:
            expected_amount = f"{hold.amount_cents / 100:.2f}"
            if existing.amount_usdc != expected_amount or existing.endpoint != endpoint:
                raise SettlementConflict("existing settlement does not match requested settlement")
            audit = self._settlement_audit(execution_id)
            if audit is None:
                raise SettlementConflict("settlement exists without its audit evidence")
            return SettlementResult(
                execution_id=execution_id,
                amount_cents=hold.amount_cents,
                ledger_id=existing.tx_hash,
                audit_hash=audit.log_hash,
                already_settled=True,
            )

        budget = self.db.execute(
            select(WorkspaceBudget)
            .where(WorkspaceBudget.workspace_id == hold.workspace_id)
            .with_for_update()
        ).scalar_one()
        if budget.balance_cents < hold.amount_cents:
            raise InsufficientBudget("reserved balance is no longer available")

        ledger_id = "local-ledger:" + hashlib.sha256(
            f"{execution_id}:{evidence_hash}:{hold.amount_cents}".encode()
        ).hexdigest()
        budget.balance_cents -= hold.amount_cents
        hold.status = HoldStatus.SETTLED
        payment = X402ConsumedPayment(
            tx_hash=ledger_id,
            wallet_address=hold.workspace_id,
            endpoint=endpoint,
            amount_usdc=f"{hold.amount_cents / 100:.2f}",
            chain_id="local-ledger",
            execution_id=execution_id,
        )
        self.db.add(payment)
        audit = AuditService(self.db).record(
            "n8n_local_budget_settled",
            {
                "execution_id": execution_id,
                "amount_cents": hold.amount_cents,
                "evidence_hash": evidence_hash,
                "ledger_id": ledger_id,
                "settlement_network": "local-ledger",
                "onchain_x402_verified": False,
            },
            workspace_id=hold.workspace_id,
            run_id=execution_id,
            forward_to_gnomledger=False,
        )
        self.db.flush()
        return SettlementResult(
            execution_id=execution_id,
            amount_cents=hold.amount_cents,
            ledger_id=ledger_id,
            audit_hash=audit.log_hash,
            already_settled=False,
        )

    def release(self, *, execution_id: str) -> WorkspaceBudgetHold:
        hold = self.db.execute(
            select(WorkspaceBudgetHold)
            .where(WorkspaceBudgetHold.execution_id == execution_id)
            .with_for_update()
        ).scalar_one_or_none()
        if hold is None:
            raise BudgetLedgerError("budget hold does not exist")
        if hold.status == HoldStatus.SETTLED:
            raise SettlementConflict("settled hold cannot be released")
        hold.status = HoldStatus.RELEASED
        self.db.flush()
        return hold

    def _settlement_audit(self, execution_id: str):
        from cappo_backend.models.audit_event import AuditEvent

        candidates = self.db.execute(
            select(AuditEvent).where(
                AuditEvent.operation_type == "n8n_local_budget_settled",
                AuditEvent.run_id == execution_id,
            )
        ).scalars()
        return next(iter(candidates), None)
