from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import cappo_backend.models  # noqa: F401
from cappo_backend.db.base import Base
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.consequence_lifecycle import ConsequenceLifecycleExecutor
from cappo_backend.services.executor import ProviderExecutionError


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _receipt(db: Session) -> None:
    actioned_at = datetime.now(UTC)
    canonical = {
        "execution_id": "exec-1",
        "mount_id": "mnt-1",
        "token_id": "tok-1",
        "principal": "operator-1",
        "caller_spiffe_id": None,
        "executor_spiffe_id": None,
        "eei_id": None,
        "profile_id": None,
        "lease_id": None,
        "operator_id": None,
        "caller_cert_sha256": None,
        "capability_id": "pkg-1",
        "biscuit_token_sha256": "biscuit",
        "action": "activation.read",
        "resource": "*",
        "policy_version": "1.0",
        "decision": "allow",
        "reason": "allowed",
        "timestamp": actioned_at.isoformat(),
        "actioned_at": actioned_at.isoformat(),
        "result_hash": None,
        "pgl_anchor_id": "anchor-1",
    }
    db.add(
        CapabilityActionReceipt(
            receipt_id="rcpt-1",
            execution_id="exec-1",
            mount_id="mnt-1",
            token_id="tok-1",
            principal="operator-1",
            action="activation.read",
            resource="*",
            decision="allow",
            reason="allowed",
            actioned_at=actioned_at,
            capability_id="pkg-1",
            biscuit_token_sha256="biscuit",
            policy_version="1.0",
            signed_receipt_cose=b"signed",
            content_hash=sha256_json(canonical),
            pgl_anchor_id="anchor-1",
        )
    )
    db.commit()


class _SuccessExecutor:
    def __init__(self, db: Session) -> None:
        self.db = db

    def execute(self, _request: dict) -> dict:
        # STARTED must already be durable before the delegate is entered.
        latest = self.db.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.operation_id == "op-1")
            .order_by(ConsequenceExecutionEvent.version.desc())
            .limit(1)
        ).scalar_one()
        assert latest.state == "started"
        return {"response": "ok", "provider": "target", "tokens": 0}


class _UncertainExecutor:
    def execute(self, _request: dict) -> dict:
        raise ProviderExecutionError("connection dropped after write")


def _states(db: Session) -> list[str]:
    return list(
        db.execute(
            select(ConsequenceExecutionEvent.state)
            .where(ConsequenceExecutionEvent.operation_id == "op-1")
            .order_by(ConsequenceExecutionEvent.version.asc())
        ).scalars()
    )


def test_success_commits_authorized_started_then_succeeded(db: Session) -> None:
    _receipt(db)
    executor = ConsequenceLifecycleExecutor(
        db=db,
        delegate=_SuccessExecutor(db),
        receipt_id="rcpt-1",
        operation_id="op-1",
        intent_hash="intent-1",
        resource="provider-dispatch",
    )

    result = executor.execute({"prompt": "go"})

    assert result["response"] == "ok"
    assert _states(db) == ["authorized", "started", "succeeded"]
    terminal = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "op-1")
        .order_by(ConsequenceExecutionEvent.version.desc())
        .limit(1)
    ).scalar_one()
    assert terminal.completion_proof_type == "callback_return"
    assert terminal.completion_proof_ref == sha256_json(result)


def test_transport_failure_is_outcome_unknown_not_failed(db: Session) -> None:
    _receipt(db)
    executor = ConsequenceLifecycleExecutor(
        db=db,
        delegate=_UncertainExecutor(),
        receipt_id="rcpt-1",
        operation_id="op-1",
        intent_hash="intent-1",
        resource="provider-dispatch",
    )

    with pytest.raises(ProviderExecutionError):
        executor.execute({"prompt": "go"})

    assert _states(db) == ["authorized", "started", "outcome_unknown"]


def test_operation_replay_cannot_dispatch_delegate_twice(db: Session) -> None:
    _receipt(db)
    executor = ConsequenceLifecycleExecutor(
        db=db,
        delegate=_SuccessExecutor(db),
        receipt_id="rcpt-1",
        operation_id="op-1",
        intent_hash="intent-1",
        resource="provider-dispatch",
    )
    executor.execute({"prompt": "go"})

    with pytest.raises(Exception, match="operation_id already exists"):
        executor.execute({"prompt": "go"})

    assert _states(db) == ["authorized", "started", "succeeded"]
