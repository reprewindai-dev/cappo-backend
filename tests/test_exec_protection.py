"""Tests for /v1/exec governed execution path (Task 5).

Regression test: /v1/exec must not permit ungoverned execution. Every request
goes through the orchestrator pipeline (PGL cert mint, EI mint, governance,
attestation).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.api.routers.exec_router import (
    ExecRequest,
    _resolve_capi_gatekeeper_public_key,
)
from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.services.run_state import RunState


class TestGovernedExecPath:
    def test_happy_path(self, client: TestClient, db: Session) -> None:
        resp = client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "echo: hello"
        assert body["run_id"] is not None
        assert body["execution_id"] is not None

    def test_missing_governance_directive_fails_closed(
        self, client: TestClient, db: Session
    ) -> None:
        resp = client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "CAPPO_GOVERNANCE_DECISION_REQUIRED"
        assert resp.json()["detail"]["fail_closed"] is True

    def test_governance_deny_fails_closed(self, client: TestClient, db: Session) -> None:
        resp = client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "DENY"}
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "CAPPO_GOVERNANCE_DENIED"

    def test_run_reaches_attested_state(self, client: TestClient, db: Session) -> None:
        client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        run = db.query(GovernedRun).first()
        assert run is not None
        assert run.state == RunState.ATTESTED.value

    def test_pgl_certificates_created_pre_and_post(self, client: TestClient, db: Session) -> None:
        client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        certs = db.query(PGLCertificate).all()
        # A pre-execution cert (commit) and a post-execution cert (attest).
        assert len(certs) == 2
        assert all(c.persisted is True for c in certs)

        pre = [c for c in certs if c.pre_execution_certificate_id is None]
        post = [c for c in certs if c.pre_execution_certificate_id is not None]
        assert len(pre) == 1 and len(post) == 1
        # Pre links forward to post; post links back to pre.
        assert pre[0].post_execution_certificate_id == post[0].certificate_id
        assert post[0].pre_execution_certificate_id == pre[0].certificate_id
        # Post cert records execution outcome hashes.
        assert post[0].output_hash is not None
        assert post[0].outcome_hash is not None

    def test_ei_row_links_post_certificate(self, client: TestClient, db: Session) -> None:
        client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        ei = db.query(ExecutionIdentity).first()
        post = (
            db.query(PGLCertificate)
            .filter(PGLCertificate.pre_execution_certificate_id.isnot(None))
            .first()
        )
        assert ei is not None and post is not None
        assert ei.pgl_post_certificate_id == post.certificate_id

    def test_execution_identity_persisted(self, client: TestClient, db: Session) -> None:
        client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        eis = db.query(ExecutionIdentity).all()
        assert len(eis) == 1
        assert eis[0].directive == "ALLOW"

    def test_audit_attestation_logged(self, client: TestClient, db: Session) -> None:
        client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        events = db.query(AuditEvent).filter(AuditEvent.operation_type == "run_attested").all()
        assert len(events) == 1

    def test_ei_contains_run_id(self, client: TestClient, db: Session) -> None:
        resp = client.post(
            "/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"}
        )
        body = resp.json()
        ei_record = db.query(ExecutionIdentity).first()
        assert ei_record is not None
        assert ei_record.run_id == body["run_id"]


class TestNoBypass:
    """Verify that no ungoverned path exists."""

    def test_no_ungoverned_exec_route(self, client: TestClient) -> None:
        # The governed /v1/exec path must exist (non-404).
        resp = client.post("/v1/exec", json={"prompt": "probe"})
        assert resp.status_code != 404, "/v1/exec must be reachable"
        # No ungoverned bypass path should exist.
        for bypass in ["/exec", "/v1/run", "/run", "/v1/execute"]:
            r = client.post(bypass, json={"prompt": "probe"})
            assert r.status_code == 404, (
                f"unexpected route {bypass} exists (status {r.status_code})"
            )


class TestCAPIGatekeeperKey:
    def test_dev_unsigned_request_keeps_existing_internal_compatibility(self) -> None:
        body = ExecRequest(prompt="hello", pgl_id="test-user-id")
        settings = Settings(environment="test")

        assert _resolve_capi_gatekeeper_public_key(settings, body) == ""

    def test_signed_request_without_configured_key_fails_closed(self) -> None:
        body = ExecRequest(
            prompt="hello",
            pgl_id="test-user-id",
            security={"nonce": "n-1", "signature": "sig"},
        )
        settings = Settings(environment="test")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_capi_gatekeeper_public_key(settings, body)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "CAPI_GATEKEEPER_KEY_UNAVAILABLE"

    def test_production_unsigned_request_fails_closed(self) -> None:
        body = ExecRequest(prompt="hello", pgl_id="test-user-id")
        settings = Settings(environment="production")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_capi_gatekeeper_public_key(settings, body)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["error"] == "CAPI_SIGNED_SECURITY_REQUIRED"
