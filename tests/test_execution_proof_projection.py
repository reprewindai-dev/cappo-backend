"""Adversarial proof-reader tests for the Activation execution chain."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import cappo_backend.models  # noqa: F401
from cappo_backend.api.routers.execution_proof_router import (
    execution_evidence_projection,
    execution_measurements_projection,
)
from cappo_backend.config import Settings
from cappo_backend.core.capi_pipeline import seal_evidence_pack
from cappo_backend.db.base import Base
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.eee import EEEBuilder, build_terminal_eee
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder
from cappo_backend.services.orchestrator import RunOrchestrator
from cappo_backend.services.pgl_client import PGLClient


class _Executor:
    def execute(self, _request: dict) -> dict:
        return {
            "response": "activation-ok",
            "provider": "activation-target",
            "model": "none",
            "tokens": 0,
            "cached": False,
        }


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


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        ei_signing_key="e" * 64,
        capability_beacon_issuer="https://cappo.veklom.com",
        capability_beacon_kid="cappo-1",
    )


def _receipt(db: Session, execution_id: str, action: str = "activation.read") -> None:
    actioned_at = datetime.now(UTC)
    canonical = {
        "execution_id": execution_id,
        "mount_id": "mnt-proof",
        "token_id": "tok-proof",
        "principal": "operator-1",
        "caller_spiffe_id": None,
        "executor_spiffe_id": None,
        "eei_id": None,
        "profile_id": None,
        "lease_id": None,
        "operator_id": None,
        "caller_cert_sha256": None,
        "capability_id": "activation-package",
        "biscuit_token_sha256": "biscuit-hash",
        "action": action,
        "resource": "*",
        "policy_version": "1.0",
        "decision": "allow",
        "reason": "allowed",
        "timestamp": actioned_at.isoformat(),
        "actioned_at": actioned_at.isoformat(),
        "result_hash": None,
        "pgl_anchor_id": "anchor-proof",
    }
    db.add(
        CapabilityActionReceipt(
            receipt_id="rcpt-proof",
            execution_id=execution_id,
            mount_id="mnt-proof",
            token_id="tok-proof",
            principal="operator-1",
            action=action,
            resource="*",
            decision="allow",
            reason="allowed",
            actioned_at=actioned_at,
            capability_id="activation-package",
            biscuit_token_sha256="biscuit-hash",
            policy_version="1.0",
            signed_receipt_cose=b"signed-proof",
            content_hash=sha256_json(canonical),
            pgl_anchor_id="anchor-proof",
        )
    )
    db.flush()


def _sealed_run(db: Session, settings: Settings) -> RunOrchestrator:
    orchestrator = RunOrchestrator(
        db=db,
        pgl=PGLClient(db=db, settings=settings),
        builder=ExecutionIdentityBuilder(signer=Ed25519Signer(settings.ei_signing_key)),
        executor=_Executor(),
        audit=AuditService(db, settings=settings),
        runtime_kind="test-runtime",
        runtime_instance="test-instance",
    )
    result = orchestrator.run_governed(
        {
            "execution_id": "exec-proof",
            "workspace_id": "ws-1",
            "tenant_id": "ws-1",
            "pgl_id": "operator-1",
            "prompt": "activation proof",
            "action": "activation.read",
            "scope": {"tools": ["activation.read"]},
            "directive": "ALLOW",
        }
    )
    assert orchestrator.last_run is not None
    builder = EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )
    envelope = build_terminal_eee(orchestrator.last_run, result=result, builder=builder)
    seal = asyncio.run(
        seal_evidence_pack(
            envelope["envelope_hash"],
            result,
            request_evidence={"data_hash": "sha256:input"},
        )
    )
    seal["eee"] = envelope
    orchestrator.record_evidence_seal(orchestrator.last_run, seal)
    _receipt(db, "exec-proof")
    db.commit()
    return orchestrator


def test_valid_proof_binds_lease_run_ei_and_pgl(db: Session, settings: Settings) -> None:
    orchestrator = _sealed_run(db, settings)
    assert orchestrator.last_run is not None
    assert orchestrator.last_run.run_id == "exec-proof"
    assert orchestrator.last_run.execution_identity["execution_id"] == "exec-proof"

    proof = execution_evidence_projection(db, "exec-proof", "ws-1", settings)

    assert proof["execution_id"] == "exec-proof"
    assert proof["authorization"]["decision"] == "allow"
    assert proof["execution_identity"]["execution_id"] == "exec-proof"
    assert proof["eee"]["execution_id"] == "exec-proof"
    assert proof["pgl"]["persisted"] is True
    assert proof["pgl"]["external"] is False
    assert proof["pgl"]["event_hash"]


def test_proof_reader_rejects_pgl_payload_tamper(db: Session, settings: Settings) -> None:
    orchestrator = _sealed_run(db, settings)
    assert orchestrator.last_run is not None
    event_id = orchestrator.last_run.pgl_identity["capi_evidence_event_id"]
    event = db.get(PGLLedgerEvent, event_id)
    assert event is not None
    event.payload = {**event.payload, "tampered": True}
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        execution_evidence_projection(db, "exec-proof", "ws-1", settings)

    assert exc_info.value.detail["error"] == "PGL_EVENT_HASH_MISMATCH"


def test_proof_reader_rejects_cross_workspace_lookup(db: Session, settings: Settings) -> None:
    _sealed_run(db, settings)

    with pytest.raises(HTTPException) as exc_info:
        execution_evidence_projection(db, "exec-proof", "ws-other", settings)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "EXECUTION_NOT_FOUND"


def test_measurements_report_only_persisted_consequence_events(
    db: Session,
    settings: Settings,
) -> None:
    _sealed_run(db, settings)
    db.add_all(
        [
            ConsequenceExecutionEvent(
                event_id="evt-auth",
                operation_id="op-1",
                intent_hash="intent-1",
                state="authorized",
                version=0,
                receipt_id="rcpt-proof",
                mount_id="mnt-proof",
                execution_id="exec-proof",
                principal="operator-1",
                action="activation.read",
                resource="activation-target",
            ),
            ConsequenceExecutionEvent(
                event_id="evt-start",
                operation_id="op-1",
                intent_hash="intent-1",
                state="started",
                version=1,
                receipt_id=None,
                mount_id="mnt-proof",
                execution_id="exec-proof",
                principal="operator-1",
                action="activation.read",
                resource="activation-target",
            ),
            ConsequenceExecutionEvent(
                event_id="evt-success",
                operation_id="op-1",
                intent_hash="intent-1",
                state="succeeded",
                version=2,
                receipt_id=None,
                mount_id="mnt-proof",
                execution_id="exec-proof",
                principal="operator-1",
                action="activation.read",
                resource="activation-target",
                completion_proof_type="callback_return",
                completion_proof_ref="target-row-1",
            ),
        ]
    )
    db.commit()

    measurements = execution_measurements_projection(db, "exec-proof", "ws-1", settings)

    assert measurements["authorization_count"] == 1
    assert measurements["consequence"]["operation_count"] == 1
    assert measurements["consequence"]["successful_count"] == 1
    assert measurements["consequence"]["failed_count"] == 0
    assert measurements["consequence"]["outcome_unknown_count"] == 0
    assert [item["state"] for item in measurements["consequence"]["events"]] == [
        "authorized",
        "started",
        "succeeded",
    ]


def test_missing_consequence_events_are_not_relabelled_as_success(
    db: Session,
    settings: Settings,
) -> None:
    _sealed_run(db, settings)

    measurements = execution_measurements_projection(db, "exec-proof", "ws-1", settings)

    assert measurements["consequence"]["operation_count"] == 0
    assert measurements["consequence"]["successful_count"] == 0
