"""EI revocation tests (PR #4).

Revocation is durable, post-issuance state. The gateway must reject a revoked EI
via the DB-backed lookup even when the replayed identity object omits the
``revoked`` flag (an attacker cannot un-revoke by stripping it).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.security.mcp_gateway import EIValidationError, MCPGateway
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.revocation_service import (
    RevocationService,
    UnknownExecutionIdentityError,
)


def _mint_ei(client: TestClient, db: Session) -> ExecutionIdentity:
    client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"})
    ei = db.query(ExecutionIdentity).first()
    assert ei is not None
    return ei


class TestRevocationService:
    def test_revoke_sets_flag_and_timestamp(self, client: TestClient, db: Session) -> None:
        ei = _mint_ei(client, db)
        service = RevocationService(db, AuditService(db))

        assert service.is_revoked(ei.execution_id) is False
        service.revoke(ei.execution_id, reason="manual")
        db.flush()

        refreshed = db.get(ExecutionIdentity, ei.execution_id)
        assert refreshed.revoked is True
        assert refreshed.revoked_at is not None
        assert service.is_revoked(ei.execution_id) is True

    def test_revoke_emits_audit_event(self, client: TestClient, db: Session) -> None:
        ei = _mint_ei(client, db)
        RevocationService(db, AuditService(db)).revoke(ei.execution_id, reason="manual")
        events = db.query(AuditEvent).filter(AuditEvent.operation_type == "ei_revoked").all()
        assert len(events) == 1
        assert events[0].payload["execution_id"] == ei.execution_id

    def test_revoke_unknown_raises(self, db: Session) -> None:
        with pytest.raises(UnknownExecutionIdentityError):
            RevocationService(db, AuditService(db)).revoke("does-not-exist")


class TestGatewayDbRevocation:
    def test_db_revocation_rejects_even_without_flag(self, client: TestClient, db: Session) -> None:
        ei = _mint_ei(client, db)
        RevocationService(db, AuditService(db)).revoke(ei.execution_id)
        db.flush()

        settings = Settings(ei_signing_key="test-signing-key", environment="test")
        audit = AuditService(db)
        revocation = RevocationService(db, audit)
        gateway = MCPGateway(audit, revocation_lookup=revocation.is_revoked, settings=settings)

        # The replayed identity object omits `revoked`, but the DB says revoked.
        identity = dict(ei.identity_json)
        identity.pop("revoked", None)

        with pytest.raises(EIValidationError, match="revoked"):
            gateway.require_execution_identity(identity, workspace_id=ei.workspace_id)


class TestRevokeEndpoint:
    def test_revoke_endpoint(self, client: TestClient, db: Session) -> None:
        ei = _mint_ei(client, db)
        resp = client.post(f"/v1/identities/{ei.execution_id}/revoke", json={"reason": "abuse"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["revoked"] is True
        assert body["revoked_at"] is not None

    def test_revoke_endpoint_unknown_returns_404(self, client: TestClient) -> None:
        resp = client.post("/v1/identities/nope/revoke", json={})
        assert resp.status_code == 404
