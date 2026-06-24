"""Enforcement-ordering tests (PR #3).

LAW 0 requires the gateway to reject an invalid ExecutionIdentity *before* any
side effect. These tests drive the orchestrator directly and prove the executor
is never invoked when the EI fails validation, and that the run ends FAILED with
a recorded ``law0_violation``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder, Ed25519Signer
from cappo_backend.services.orchestrator import RunOrchestrator
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
    db: Session, *, mint_key: str, gateway_key: str
) -> tuple[RunOrchestrator, _SpyExecutor, MCPGateway]:
    settings = Settings(ei_signing_key=gateway_key, environment="test")
    pgl = PGLClient(db=db, settings=settings)
    builder = ExecutionIdentityBuilder(signer=Ed25519Signer(mint_key))
    audit = AuditService(db)
    gateway = MCPGateway(audit, pgl_lookup=pgl.get_certificate, settings=settings)
    executor = _SpyExecutor()
    orch = RunOrchestrator(
        db=db, pgl=pgl, builder=builder, executor=executor, audit=audit, gateway=gateway
    )
    return orch, executor, gateway


def test_invalid_ei_blocks_side_effect(db: Session) -> None:
    # EI is minted with one key but the gateway verifies with another → rule 8
    # signature check fails *before* the executor runs.
    orch, executor, _ = _orchestrator(db, mint_key="mint-key", gateway_key="other-key")

    with pytest.raises(EIValidationError):
        orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id"})

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

    result = orch.run_governed({"prompt": "hi", "pgl_id": "test-user-id"})

    assert executor.called is True
    assert result["response"] == "should-not-happen"  # spy executor's output
    assert orch.last_run is not None
    assert orch.last_run.state == RunState.ATTESTED.value
