"""Tests for /v1/exec governed execution path (Task 5).

Regression test: /v1/exec must not permit ungoverned execution. Every request
goes through the orchestrator pipeline (PGL cert mint, EI mint, governance,
attestation).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.services.run_state import RunState


class TestGovernedExecPath:
    def test_happy_path(self, client: TestClient, db: Session) -> None:
        resp = client.post("/v1/exec", json={"prompt": "hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "echo: hello"
        assert body["run_id"] is not None
        assert body["execution_id"] is not None

    def test_run_reaches_attested_state(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        run = db.query(GovernedRun).first()
        assert run is not None
        assert run.state == RunState.ATTESTED.value

    def test_pgl_certificates_created_pre_and_post(
        self, client: TestClient, db: Session
    ) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
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

    def test_ei_row_links_post_certificate(
        self, client: TestClient, db: Session
    ) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        ei = db.query(ExecutionIdentity).first()
        post = (
            db.query(PGLCertificate)
            .filter(PGLCertificate.pre_execution_certificate_id.isnot(None))
            .first()
        )
        assert ei is not None and post is not None
        assert ei.pgl_post_certificate_id == post.certificate_id

    def test_execution_identity_persisted(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        eis = db.query(ExecutionIdentity).all()
        assert len(eis) == 1
        assert eis[0].directive == "ALLOW"

    def test_audit_attestation_logged(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        events = db.query(AuditEvent).filter(
            AuditEvent.operation_type == "run_attested"
        ).all()
        assert len(events) == 1

    def test_ei_contains_run_id(self, client: TestClient, db: Session) -> None:
        resp = client.post("/v1/exec", json={"prompt": "hello"})
        body = resp.json()
        ei_record = db.query(ExecutionIdentity).first()
        assert ei_record is not None
        assert ei_record.run_id == body["run_id"]


class TestNoBypass:
    """Verify that no ungoverned path exists."""

    def test_no_ungoverned_exec_route(self, client: TestClient) -> None:
        # The only execution route is the governed /v1/exec.
        routes = {r.path for r in client.app.routes if hasattr(r, "path")}
        exec_routes = {p for p in routes if p.endswith("/exec")}
        assert exec_routes == {"/v1/exec"}, f"unexpected exec routes: {exec_routes}"
