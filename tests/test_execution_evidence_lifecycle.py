"""PGL lifecycle proof independent of API/payment optional dependencies."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import cappo_backend.models  # noqa: F401
from cappo_backend.config import Settings
from cappo_backend.core.capi_pipeline import seal_evidence_pack
from cappo_backend.db.base import Base
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.eee import (
    EEEBuilder,
    EEEVerifier,
    VerificationVerdict,
    build_terminal_eee,
)
from cappo_backend.services.ei_builder import Ed25519Signer, ExecutionIdentityBuilder
from cappo_backend.services.orchestrator import GovernanceDeniedError, RunOrchestrator
from cappo_backend.services.pgl_client import PGLClient


class _Executor:
    provider = "test-provider"
    model = "test-model"

    def execute(self, request: dict) -> dict:
        return {"response": "ok", "provider": self.provider, "model": self.model}


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def _orchestrator(db: Session) -> RunOrchestrator:
    settings = Settings(environment="test", ei_signing_key="same-key")
    return RunOrchestrator(
        db=db,
        pgl=PGLClient(db=db, settings=settings),
        builder=ExecutionIdentityBuilder(signer=Ed25519Signer("same-key")),
        executor=_Executor(),
        audit=AuditService(db, settings=settings),
        runtime_kind="test-runtime",
        runtime_instance="test-instance",
    )


def test_denied_run_mints_a_persisted_pgl_certificate(db: Session) -> None:
    orchestrator = _orchestrator(db)

    with pytest.raises(GovernanceDeniedError):
        orchestrator.run_governed({"prompt": "denied", "directive": "DENY"})

    assert orchestrator.last_run is not None
    certificate_id = orchestrator.last_run.pgl_identity["pre_execution_certificate_id"]
    certificate = db.get(PGLCertificate, certificate_id)
    assert certificate is not None
    assert certificate.persisted is True
    assert certificate.governance_decision == "DENY"


def test_execution_identity_reuses_the_semantic_run_id(db: Session) -> None:
    orchestrator = _orchestrator(db)

    orchestrator.run_governed({"prompt": "allowed", "directive": "ALLOW"})

    assert orchestrator.last_run is not None
    assert orchestrator.last_run.execution_identity["execution_id"] == orchestrator.last_run.run_id


def test_pgl_records_capi_request_and_result_seal(db: Session) -> None:
    orchestrator = _orchestrator(db)
    orchestrator.run_governed({"prompt": "allowed", "directive": "ALLOW"})
    assert orchestrator.last_run is not None
    post_certificate_id = orchestrator.last_run.pgl_identity["post_execution_certificate_id"]

    event = orchestrator.record_evidence_seal(
        orchestrator.last_run,
        {
            "evidence_id": "sha256:request",
            "request_evidence": {"data_hash": "sha256:input"},
            "result_hash": "sha256:result",
            "seal_hash": "sha256:seal",
        },
    )

    persisted = db.get(PGLLedgerEvent, event.event_id)
    assert persisted is not None
    assert persisted.certificate_id == post_certificate_id
    assert persisted.event_type == "capi_evidence_sealed"
    assert persisted.payload["evidence_seal"]["result_hash"] == "sha256:result"
    assert orchestrator.last_run.pgl_identity["capi_evidence_event_id"] == event.event_id


def test_capi_seal_carries_only_committed_request_and_result_evidence() -> None:
    seal = asyncio.run(
        seal_evidence_pack(
            "sha256:request",
            {"response": "result"},
            request_evidence={"data_hash": "sha256:input", "security_hash": "sha256:security"},
        )
    )

    assert seal["request_evidence"] == {
        "data_hash": "sha256:input",
        "security_hash": "sha256:security",
    }
    assert seal["result_hash"]


def test_terminal_eee_binds_an_allowed_run_to_its_semantic_execution_id(db: Session) -> None:
    orchestrator = _orchestrator(db)
    result = orchestrator.run_governed({"prompt": "allowed", "directive": "ALLOW"})
    assert orchestrator.last_run is not None
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")

    envelope = build_terminal_eee(orchestrator.last_run, result=result, builder=builder)

    verification = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)
    assert verification.verdict is VerificationVerdict.VALID_WITH_UNRESOLVED_REFS
    assert envelope["execution_id"] == orchestrator.last_run.run_id
    assert envelope["status"] == "completed"


def test_terminal_eee_mints_a_signed_denial_without_provider_execution(db: Session) -> None:
    orchestrator = _orchestrator(db)
    with pytest.raises(GovernanceDeniedError):
        orchestrator.run_governed({"prompt": "denied", "directive": "DENY"})
    assert orchestrator.last_run is not None
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")

    envelope = build_terminal_eee(orchestrator.last_run, result=None, builder=builder)

    verification = EEEVerifier({"cappo-1": builder.public_key_bytes}).verify(envelope)
    assert verification.verdict is VerificationVerdict.VALID_WITH_UNRESOLVED_REFS
    assert envelope["execution_id"] == orchestrator.last_run.run_id
    assert envelope["status"] == "denied"
    assert envelope["actual_effects"] == []


def test_pgl_seals_a_terminal_denial_against_its_pre_execution_certificate(db: Session) -> None:
    orchestrator = _orchestrator(db)
    with pytest.raises(GovernanceDeniedError):
        orchestrator.run_governed({"prompt": "denied", "directive": "DENY"})
    assert orchestrator.last_run is not None
    builder = EEEBuilder(signing_key="e" * 64, issuer="https://cappo.veklom.com", kid="cappo-1")
    envelope = build_terminal_eee(orchestrator.last_run, result=None, builder=builder)

    event = orchestrator.record_evidence_seal(
        orchestrator.last_run,
        {"evidence_id": envelope["envelope_hash"], "seal_hash": envelope["envelope_hash"], "eee": envelope},
    )

    persisted = db.get(PGLLedgerEvent, event.event_id)
    assert persisted is not None
    assert persisted.certificate_id == orchestrator.last_run.pgl_identity["pre_execution_certificate_id"]
    assert persisted.payload["evidence_seal"]["eee"]["status"] == "denied"
