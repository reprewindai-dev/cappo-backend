"""Enforcement-ordering tests (PR #3).

LAW 0 requires the gateway to reject an invalid ExecutionIdentity *before* any
side effect. These tests drive the orchestrator directly and prove the executor
is never invoked when the EI fails validation, and that the run ends FAILED with
a recorded ``law0_violation``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.runtime_path_assignment import RuntimePathAssignment
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder
from cappo_backend.services.orchestrator import (
    RunOrchestrator,
    RuntimeOwnershipError,
)
from cappo_backend.services.pgl_client import PGLClient
from cappo_backend.services.run_state import RunState


class _SpyExecutor:
    provider = "spy"
    model = "spy-1"

    def __init__(self) -> None:
        self.called = False

    def execute(self, request: dict) -> dict:
        self.called = True
        return {"response": "should-not-happen", "model": self.model, "provider": self.provider}


def _orchestrator(
    db: Session,
    *,
    mint_key: str,
    gateway_key: str,
    runtime_kind: str = "amphoteric",
    runtime_instance: str = "runtime-a",
) -> tuple[RunOrchestrator, _SpyExecutor, MCPGateway]:
    settings = Settings(ei_signing_key=gateway_key, environment="test")
    pgl = PGLClient(db=db, settings=settings)
    builder = ExecutionIdentityBuilder(signer=Ed25519Signer(mint_key))
    audit = AuditService(db)
    gateway = MCPGateway(audit, pgl_lookup=pgl.get_certificate, settings=settings)
    executor = _SpyExecutor()
    orch = RunOrchestrator(
        db=db,
        pgl=pgl,
        builder=builder,
        executor=executor,
        audit=audit,
        gateway=gateway,
        runtime_kind=runtime_kind,
        runtime_instance=runtime_instance,
    )
    return orch, executor, gateway


def test_invalid_ei_blocks_side_effect(db: Session) -> None:
    # EI is minted with one key but the gateway verifies with another → rule 8
    # signature check fails *before* the executor runs.
    orch, executor, _ = _orchestrator(db, mint_key="mint-key", gateway_key="other-key")

    with pytest.raises(EIValidationError):
        orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})

    assert executor.called is False, "executor ran despite invalid EI (LAW 0 breach)"
    assert orch.last_run is not None
    assert orch.last_run.state == RunState.FAILED.value

    violations = (
        db.query(AuditEvent)
        .filter(AuditEvent.operation_type == "law0_violation")
        .all()
    )
    assert len(violations) == 1


def test_valid_ei_allows_side_effect(db: Session) -> None:
    orch, executor, _ = _orchestrator(db, mint_key="same-key", gateway_key="same-key")

    result = orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})

    assert executor.called is True
    assert result["response"] == "should-not-happen"  # spy executor's output
    assert orch.last_run is not None
    assert orch.last_run.state == RunState.ATTESTED.value


def test_execution_identity_binds_path_owner_and_epoch(db: Session) -> None:
    orch, executor, _ = _orchestrator(
        db,
        mint_key="same-key",
        gateway_key="same-key",
        runtime_kind="amphoteric",
        runtime_instance="runtime-a",
    )

    orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})

    assert executor.called is True
    assert orch.last_run is not None
    identity = orch.last_run.execution_identity or {}
    ownership = identity["runtime_ownership"]
    assert ownership["path_id"] == orch.last_run.run_id
    assert ownership["assignment_id"]
    assert ownership["authority_epoch"] == 1
    assert ownership["runtime_kind"] == "amphoteric"
    assert ownership["runtime_instance"] == "runtime-a"


def test_wrong_runtime_owner_fails_before_side_effect(db: Session) -> None:
    orch, executor, _ = _orchestrator(
        db,
        mint_key="same-key",
        gateway_key="same-key",
        runtime_kind="amphoteric",
        runtime_instance="runtime-a",
    )
    original_mint = orch.mint_execution_identity

    def mint_for_other_runtime(run) -> None:
        orch._runtime_instance = "runtime-b"
        original_mint(run)
        orch._runtime_instance = "runtime-a"

    orch.mint_execution_identity = mint_for_other_runtime  # type: ignore[method-assign]

    with pytest.raises(RuntimeOwnershipError, match="RUNTIME_OWNER_MISMATCH"):
        orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})

    assert executor.called is False


def test_reassignment_requires_new_epoch_and_assignment(db: Session) -> None:
    orch, executor, _ = _orchestrator(
        db,
        mint_key="same-key",
        gateway_key="same-key",
        runtime_kind="amphoteric",
        runtime_instance="runtime-a",
    )
    original_mint = orch.mint_execution_identity

    def mint_conflicting_assignment(run) -> None:
        original_mint(run)
        ownership = dict((run.execution_identity or {})["runtime_ownership"])
        ownership["previous_assignment_id"] = ownership["assignment_id"]
        ownership["runtime_instance"] = "runtime-b"
        record = db.get(RuntimePathAssignment, ownership["assignment_id"])
        assert record is not None
        record.runtime_instance = "runtime-b"
        db.flush()
        unsigned = {
            key: value
            for key, value in (run.execution_identity or {}).items()
            if key not in {"hash", "signature"}
        }
        run.execution_identity = orch._builder._finalize_and_sign({
            **unsigned,
            "runtime_ownership": ownership,
        })

    orch.mint_execution_identity = mint_conflicting_assignment  # type: ignore[method-assign]

    with pytest.raises(RuntimeOwnershipError, match="AUTHORITY_EPOCH_NOT_ADVANCED"):
        orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})

    assert executor.called is False


def test_existing_path_assignment_cannot_be_silently_reassigned(db: Session) -> None:
    orch, executor, _ = _orchestrator(
        db,
        mint_key="same-key",
        gateway_key="same-key",
        runtime_kind="amphoteric",
        runtime_instance="runtime-a",
    )
    run = orch.create_run({"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW"})
    db.add(
        RuntimePathAssignment(
            path_id=run.run_id,
            assignment_id="assignment-a",
            authority_epoch=1,
            runtime_kind="amphoteric",
            runtime_instance="runtime-a",
        )
    )
    db.flush()
    orch.compile_run(run)
    orch.contextualize_run(run)
    orch.govern_run(run)
    orch.commit_run(run)

    with pytest.raises(RuntimeOwnershipError, match="PATH_ALREADY_ASSIGNED"):
        orch.mint_execution_identity(run)

    assert executor.called is False
    assignment = db.execute(
        select(RuntimePathAssignment).where(RuntimePathAssignment.path_id == run.run_id)
    ).scalar_one()
    assert assignment.assignment_id == "assignment-a"
    assert assignment.runtime_instance == "runtime-a"
