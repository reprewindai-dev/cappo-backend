"""Tests for /v1/exec governed execution path (Task 5).

Regression test: /v1/exec must not permit ungoverned execution. Every request
goes through the orchestrator pipeline (PGL cert mint, EI mint, governance,
attestation).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from starlette.requests import Request

from cappo_backend.api.routers.exec_router import (
    ExecRequest,
    _build_capi_payload,
    _execute_run,
    _resolve_capi_gatekeeper_public_key,
    _verify_exec_request_integrity,
)
from cappo_backend.config import Settings
from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.services.orchestrator import RuntimeOwnershipError
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
        assert body["links"] == {
            "evidence": {
                "href": f"/v1/executions/{body['execution_id']}/evidence",
                "method": "GET",
            },
            "measurements": {
                "href": f"/v1/executions/{body['execution_id']}/measurements",
                "method": "GET",
            },
        }

    def test_missing_governance_directive_fails_closed(
        self, client: TestClient, db: Session
    ) -> None:
        resp = client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "CAPPO_GOVERNANCE_DECISION_REQUIRED"
        assert resp.json()["detail"]["fail_closed"] is True

    def test_stale_target_observation_is_rejected_before_execution(
        self, client: TestClient, settings: Settings
    ) -> None:
        observer = Ed25519PrivateKey.generate()
        settings.vnp_federation_public_key = observer.public_key().public_bytes_raw().hex()
        observation = {
            "target_id": "repo:owner/project@main",
            "observed_state_hash": "sha256:current",
            "observed_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
        }
        signature = observer.sign(
            json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
        ).hex()

        response = client.post(
            "/v1/exec",
            json={
                "prompt": "update repository",
                "pgl_id": "test-user-id",
                "directive": "ALLOW",
                "target_precondition": {
                    **observation,
                    "expected_state_hash": "sha256:expected-old",
                    "signature": signature,
                },
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "STALE_TARGET"

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
        for bypass in [
            "/exec",
            "/v1/run",
            "/run",
            "/v1/execute",
            "/api/fpi/execute",
        ]:
            r = client.post(bypass, json={"prompt": "probe"})
            assert r.status_code == 404, (
                f"unexpected route {bypass} exists (status {r.status_code})"
            )


class TestCAPIGatekeeperKey:
    def test_signed_payload_does_not_hash_its_own_signature(self) -> None:
        body = ExecRequest(
            prompt="deny-path probe",
            pgl_id="production-verifier",
            directive="DENY",
            security={"nonce": "nonce-1", "signature": "signature-1"},
        )

        payload = _build_capi_payload(body)

        assert payload["security"] == body.security
        assert "security" not in payload["data"]

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

    def test_rfc9421_integrity_is_checked_before_cappo_authority(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        body = b'{"prompt":"governed"}'
        request = _signed_exec_request(private_key, body)

        asyncio.run(
            _verify_exec_request_integrity(
                request,
                private_key.public_key().public_bytes_raw().hex(),
            )
        )

    def test_tampered_exec_request_is_rejected_before_cappo_authority(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        request = _signed_exec_request(
            private_key,
            b'{"prompt":"governed"}',
            tamper_body=True,
        )

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                _verify_exec_request_integrity(
                    request,
                    private_key.public_key().public_bytes_raw().hex(),
                )
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail["error"] == "HTTP_MESSAGE_INTEGRITY_INVALID"


def test_runtime_ownership_conflict_is_terminal_http_409(db: Session) -> None:
    class _OwnershipConflictOrchestrator:
        def run_governed(self, _payload):
            raise RuntimeOwnershipError("RUNTIME_OWNER_MISMATCH")

    with pytest.raises(HTTPException) as exc_info:
        _execute_run(_OwnershipConflictOrchestrator(), {}, db)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "error": "RUNTIME_OWNERSHIP_CONFLICT",
        "detail": "RUNTIME_OWNER_MISMATCH",
        "fail_stop": True,
        "retryable": False,
    }


def _signed_exec_request(
    private_key: Ed25519PrivateKey,
    signed_body: bytes,
    *,
    tamper_body: bool = False,
) -> Request:
    actual_body = b'{"prompt":"tampered"}' if tamper_body else signed_body
    digest = f"sha-256=:{base64.b64encode(hashlib.sha256(signed_body).digest()).decode('ascii')}:"
    created = int(datetime.now(UTC).timestamp())
    params = f';created={created};keyid="requester-1"'
    target = "https://cappo.veklom.com/v1/exec"
    signature_base = "\n".join(
        [
            '"@method": POST',
            f'"@target-uri": {target}',
            f'"content-digest": {digest}',
            f'"@signature-params": ("@method" "@target-uri" "content-digest"){params}',
        ]
    )
    signature = base64.b64encode(private_key.sign(signature_base.encode())).decode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": actual_body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("cappo.veklom.com", 443),
            "path": "/v1/exec",
            "headers": [
                (b"host", b"cappo.veklom.com"),
                (b"content-digest", digest.encode()),
                (b"signature-input", f'sig1=("@method" "@target-uri" "content-digest"){params}'.encode()),
                (b"signature", f"sig1=:{signature}:".encode()),
            ],
        },
        receive=receive,
    )
