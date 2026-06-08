"""Tests for MCP Gateway validation — all 9 rules (Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.ei_builder import ExecutionIdentityBuilder, HmacSigner

SIGNING_KEY = "test-signing-key"


def _valid_ei(**overrides: object) -> dict:
    signer = HmacSigner(SIGNING_KEY)
    builder = ExecutionIdentityBuilder(signer=signer)
    inputs = {
        "pgl_pre_certificate_id": "cert-1",
        "genome_hash": "g",
        "constitution_hash": "c",
        "plan_hash": "p",
        "directive": "ALLOW",
        "risk_tier": "standard",
        "scope": {"tools": ["llm.exec"]},
        "issuer": "test",
        "execution_id": "ei-1",
        "issued_at": datetime.now(timezone.utc),
        "ttl_seconds": 86400,
    }
    inputs.update(overrides)
    return builder.build(inputs)


def _make_cert(db: Session, cert_id: str = "cert-1", **overrides: object) -> PGLCertificate:
    defaults = dict(
        certificate_id=cert_id,
        run_id="r1",
        workspace_id="ws",
        genome_hash="g",
        constitution_hash="c",
        plan_hash="p",
        governance_decision="ALLOW",
        risk_tier="standard",
    )
    defaults.update(overrides)
    cert = PGLCertificate(**defaults)
    db.add(cert)
    db.flush()
    return cert


@pytest.fixture
def audit(db: Session) -> AuditService:
    return AuditService(db)


@pytest.fixture
def gateway(db: Session, audit: AuditService, settings: Settings) -> MCPGateway:
    def lookup(cert_id: str) -> PGLCertificate | None:
        return db.get(PGLCertificate, cert_id)
    return MCPGateway(audit, pgl_lookup=lookup, settings=settings)


class TestRule1PersistedPGL:
    def test_missing_cert_id(self, gateway: MCPGateway) -> None:
        ei = _valid_ei()
        ei["pgl_pre_certificate_id"] = ""
        with pytest.raises(EIValidationError, match="missing"):
            gateway.require_execution_identity(ei)

    def test_cert_not_found(self, gateway: MCPGateway) -> None:
        ei = _valid_ei(pgl_pre_certificate_id="nonexistent")
        with pytest.raises(EIValidationError, match="not found"):
            gateway.require_execution_identity(ei)

    def test_cert_not_persisted(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db, persisted=False)
        ei = _valid_ei()
        with pytest.raises(EIValidationError, match="not persisted"):
            gateway.require_execution_identity(ei)


class TestRule2HashAlignment:
    def test_genome_hash_mismatch(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db, genome_hash="different")
        ei = _valid_ei()
        with pytest.raises(EIValidationError, match="genome_hash mismatch"):
            gateway.require_execution_identity(ei)


class TestRule3Directive:
    def test_deny_directive(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei(directive="DENY")
        with pytest.raises(EIValidationError, match="does not permit"):
            gateway.require_execution_identity(ei)

    def test_allow_with_audit_ok(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei(directive="ALLOW_WITH_AUDIT")
        gateway.require_execution_identity(ei)


class TestRule4TTL:
    def test_expired(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        signer = HmacSigner(SIGNING_KEY)
        builder = ExecutionIdentityBuilder(signer=signer)
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        ei = builder.build({
            "pgl_pre_certificate_id": "cert-1",
            "genome_hash": "g", "constitution_hash": "c", "plan_hash": "p",
            "directive": "ALLOW", "risk_tier": "standard",
            "scope": {"tools": ["llm.exec"]}, "issuer": "t",
            "issued_at": past, "expires_at": past + timedelta(seconds=1),
        })
        with pytest.raises(EIValidationError, match="expired"):
            gateway.require_execution_identity(ei)


class TestRule5Scope:
    def test_action_not_in_scope(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei(scope={"tools": ["llm.exec"]})
        with pytest.raises(EIValidationError, match="scope"):
            gateway.require_execution_identity(ei, action="file.write")


class TestRule6Budget:
    def test_insufficient_budget(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei(budget_approved_cents=10)
        with pytest.raises(EIValidationError, match="budget"):
            gateway.require_execution_identity(ei, action_cost_cents=100)


class TestRule7DelegationDepth:
    def test_exceeds_max(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei(delegation_depth=99)
        with pytest.raises(EIValidationError, match="delegation depth"):
            gateway.require_execution_identity(ei)


class TestRule8SignatureHash:
    def test_tampered_hash(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei()
        ei["hash"] = "tampered"
        with pytest.raises(EIValidationError, match="hash verification"):
            gateway.require_execution_identity(ei)

    def test_tampered_signature(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei()
        ei["signature"] = "tampered"
        with pytest.raises(EIValidationError, match="signature verification"):
            gateway.require_execution_identity(ei)


class TestRule9Revoked:
    def test_revoked(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei()
        ei["revoked"] = True
        with pytest.raises(EIValidationError, match="revoked"):
            gateway.require_execution_identity(ei)


class TestMissingIdentity:
    def test_none_identity(self, gateway: MCPGateway) -> None:
        with pytest.raises(EIValidationError, match="missing"):
            gateway.require_execution_identity(None)


class TestValidIdentityPasses:
    def test_valid_identity(self, db: Session, gateway: MCPGateway) -> None:
        _make_cert(db)
        ei = _valid_ei()
        gateway.require_execution_identity(ei)


class TestAuditOnFailure:
    def test_law0_event_logged(self, db: Session, gateway: MCPGateway) -> None:
        from cappo_backend.models.audit_event import AuditEvent
        _make_cert(db)
        ei = _valid_ei(directive="DENY")
        with pytest.raises(EIValidationError):
            gateway.require_execution_identity(ei, workspace_id="ws1")
        events = db.query(AuditEvent).filter(AuditEvent.operation_type == "law0_violation").all()
        assert len(events) == 1
        assert events[0].payload["law0"] is True
