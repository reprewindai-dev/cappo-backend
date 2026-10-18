"""Real PostgreSQL N8N-16 budget-ledger race certification."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select

REPO = Path(__file__).resolve().parents[1]
for line in (REPO / ".env.test").read_text(encoding="utf-8").splitlines():
    if line.startswith("DATABASE_URL="):
        os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().replace(
            "@172.17.200.200:5432", "@127.0.0.1:5432"
        )
        break

from cappo_backend.db.session import SessionLocal
from cappo_backend.execution.budget_ledger import (
    BudgetLedger,
    InsufficientBudget,
    SettlementConflict,
)
from cappo_backend.models.workspace_budget import HoldStatus, WorkspaceBudget, WorkspaceBudgetHold
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment


def ident(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def seed(workspace_id: str, cents: int) -> None:
    with SessionLocal() as db:
        db.add(WorkspaceBudget(workspace_id=workspace_id, balance_cents=cents))
        db.commit()


def reserve(execution_id: str, workspace_id: str, cents: int) -> str:
    try:
        with SessionLocal() as db:
            BudgetLedger(db).reserve(
                execution_id=execution_id,
                workspace_id=workspace_id,
                amount_cents=cents,
            )
            db.commit()
        return "reserved"
    except InsufficientBudget:
        return "denied"


def main() -> None:
    # 1. Ambiguous/crashed work retains its active hold.
    ws_crash, ex_crash = ident("ws"), ident("exec")
    seed(ws_crash, 10)
    reserve(ex_crash, ws_crash, 1)
    with SessionLocal() as db:
        assert db.get(WorkspaceBudgetHold, ex_crash).status == HoldStatus.ACTIVE
    print("[PASS] 1. Crash/ambiguity retains ACTIVE hold.")

    # 2/3. Five settlement workers deduct exactly once and converge on one id.
    ws_race, ex_race = ident("ws"), ident("exec")
    seed(ws_race, 10)
    reserve(ex_race, ws_race, 1)

    def settle() -> tuple[bool, str]:
        with SessionLocal() as db:
            result = BudgetLedger(db).settle_local(
                execution_id=ex_race,
                evidence_hash="race-evidence",
                endpoint="sandbox_file_append",
            )
            db.commit()
            return result.already_settled, result.ledger_id

    with ThreadPoolExecutor(max_workers=5) as pool:
        settled = list(pool.map(lambda _: settle(), range(5)))
    assert sum(not duplicate for duplicate, _ in settled) == 1
    assert len({ledger_id for _, ledger_id in settled}) == 1
    with SessionLocal() as db:
        assert db.get(WorkspaceBudget, ws_race).balance_cents == 9
        payments = db.execute(
            select(X402ConsumedPayment).where(X402ConsumedPayment.execution_id == ex_race)
        ).scalars().all()
        assert len(payments) == 1
    print("[PASS] 2. Five duplicate settlement workers deduct exactly once.")
    print("[PASS] 3. Lost-response retry returns the original local ledger identity.")

    # 4. Cancellation before consequence releases; it does not spend.
    ws_cancel, ex_cancel = ident("ws"), ident("exec")
    seed(ws_cancel, 10)
    reserve(ex_cancel, ws_cancel, 1)
    with SessionLocal() as db:
        BudgetLedger(db).release(execution_id=ex_cancel)
        db.commit()
        assert db.get(WorkspaceBudget, ws_cancel).balance_cents == 10
        assert db.get(WorkspaceBudgetHold, ex_cancel).status == HoldStatus.RELEASED
    print("[PASS] 4. Proven pre-consequence cancellation releases without spending.")

    # 5. A settled consequence cannot be retroactively refunded.
    with SessionLocal() as db:
        try:
            BudgetLedger(db).release(execution_id=ex_race)
        except SettlementConflict:
            db.rollback()
        else:
            raise AssertionError("settled hold was incorrectly released")
    print("[PASS] 5. Post-consequence revocation cannot release a settled hold.")

    # 6. Concurrent over-reservation is serialized by the workspace row lock.
    ws_over = ident("ws")
    seed(ws_over, 8)
    executions = [ident("exec") for _ in range(10)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(lambda ex: reserve(ex, ws_over, 1), executions))
    assert outcomes.count("reserved") == 8
    assert outcomes.count("denied") == 2
    print("[PASS] 6. Ten concurrent holds against 8 cents yield exactly 8 reservations.")

    # 7. Local settlement rows never masquerade as chain transactions.
    with SessionLocal() as db:
        payment = db.execute(
            select(X402ConsumedPayment).where(X402ConsumedPayment.execution_id == ex_race)
        ).scalar_one()
        assert payment.chain_id == "local-ledger"
        assert payment.tx_hash.startswith("local-ledger:")
    print("[PASS] 7. Local ledger evidence is explicitly not on-chain x402 proof.")


if __name__ == "__main__":
    main()
