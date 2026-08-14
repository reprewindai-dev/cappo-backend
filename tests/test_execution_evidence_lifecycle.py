"""PGL lifecycle proof independent of API/payment optional dependencies."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import cappo_backend.models  # noqa: F401
from cappo_backend.config import Settings
from cappo_backend.db.base import Base
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.services.audit_service import AuditService
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
