"""Independent target-state tests for the Veklom Activation consequence."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cappo_backend.db.base import Base
from cappo_backend.models.activation_consequence import ActivationConsequence
from cappo_backend.services.activation_target import (
    ACTIVATION_WRITE_ACTION,
    ActivationTargetExecutor,
    ActivationTargetInvariantError,
    observe_activation_consequence,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _request(**overrides: str) -> dict[str, str]:
    payload = {
        "action": ACTIVATION_WRITE_ACTION,
        "workspace_id": "ws-activation",
        "execution_id": "exec-activation-1",
        "capability_execution_id": "exec-activation-1",
        "capability_mount_id": "mount-activation-1",
        "capability_receipt_id": "receipt-activation-1",
    }
    payload.update(overrides)
    return payload


def _row_count(db: Session) -> int:
    return int(
        db.execute(select(func.count(ActivationConsequence.consequence_id))).scalar_one()
    )


def test_activation_target_creates_one_durable_consequence(db: Session) -> None:
    result = ActivationTargetExecutor(db).execute(_request())

    assert _row_count(db) == 1
    assert result["provider"] == "veklom-activation-target"
    target = result["activation_target"]
    assert target["execution_id"] == "exec-activation-1"
    assert target["workspace_id"] == "ws-activation"
    assert target["receipt_id"] == "receipt-activation-1"
    assert target["idempotent_replay"] is False

    observed = observe_activation_consequence(
        db,
        execution_id="exec-activation-1",
        workspace_id="ws-activation",
    ).as_dict()
    assert observed["consequence_count"] == 1
    assert observed["content_hash"] == target["content_hash"]
    assert observed["persisted"] is True


def test_activation_target_is_independently_idempotent(db: Session) -> None:
    executor = ActivationTargetExecutor(db)
    first = executor.execute(_request())
    second = executor.execute(_request())

    assert _row_count(db) == 1
    assert first["activation_target"]["consequence_id"] == second["activation_target"]["consequence_id"]
    assert second["activation_target"]["idempotent_replay"] is True


def test_activation_target_conflicting_duplicate_fails_closed(db: Session) -> None:
    executor = ActivationTargetExecutor(db)
    executor.execute(_request())

    with pytest.raises(ActivationTargetInvariantError):
        executor.execute(_request(capability_receipt_id="different-receipt"))

    assert _row_count(db) == 1


def test_activation_target_requires_server_bound_lease_provenance(db: Session) -> None:
    payload = _request()
    del payload["capability_receipt_id"]

    with pytest.raises(ActivationTargetInvariantError):
        ActivationTargetExecutor(db).execute(payload)

    assert _row_count(db) == 0


def test_activation_observer_is_workspace_bound(db: Session) -> None:
    ActivationTargetExecutor(db).execute(_request())

    other_workspace = observe_activation_consequence(
        db,
        execution_id="exec-activation-1",
        workspace_id="ws-other",
    ).as_dict()
    assert other_workspace["consequence_count"] == 0
    assert other_workspace["consequence_id"] is None
    assert other_workspace["persisted"] is False


def test_activation_target_completion_proof_reobserves_persisted_row(db: Session) -> None:
    executor = ActivationTargetExecutor(db)
    result = executor.execute(_request())

    proof_type, proof_ref = executor.completion_proof(result)

    observed = observe_activation_consequence(
        db,
        execution_id="exec-activation-1",
        workspace_id="ws-activation",
    ).as_dict()
    assert proof_type == "durable_target_row"
    assert proof_ref == observed["content_hash"]
    assert observed["consequence_count"] == 1


def test_activation_target_completion_proof_rejects_tampered_result(db: Session) -> None:
    executor = ActivationTargetExecutor(db)
    result = executor.execute(_request())
    result["activation_target"]["content_hash"] = "tampered"

    with pytest.raises(ActivationTargetInvariantError):
        executor.completion_proof(result)
