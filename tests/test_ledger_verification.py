"""Tests for the ledger chain verification service + endpoints (Phase 5).

Covers tamper-evidence over the global audit chain and the per-certificate PGL
ledger chain: a clean chain verifies, a mutated node is caught (node integrity),
and a removed node is caught (link integrity).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.ledger_verifier import LedgerVerifier


def _seed_audit_chain(db: Session, n: int = 3) -> None:
    audit = AuditService(db)
    for i in range(n):
        audit.record("test_event", {"i": i})
    db.flush()


class TestAuditChain:
    def test_empty_chain_is_valid(self, db: Session) -> None:
        report = LedgerVerifier(db).verify_audit_chain()
        assert report.valid is True
        assert report.total == 0

    def test_clean_chain_verifies(self, db: Session) -> None:
        _seed_audit_chain(db, 3)
        report = LedgerVerifier(db).verify_audit_chain()
        assert report.valid is True
        assert report.total == 3
        assert report.broken_links == []

    def test_tampered_payload_detected(self, db: Session) -> None:
        _seed_audit_chain(db, 3)
        victim = db.query(AuditEvent).order_by(AuditEvent.created_at.asc()).all()[1]
        # Mutate contents without recomputing the stored hash.
        victim.payload = {"i": 999, "tampered": True}
        db.flush()

        report = LedgerVerifier(db).verify_audit_chain()
        assert report.valid is False
        broken_ids = {b["id"] for b in report.broken_links}
        assert victim.log_id in broken_ids

    def test_removed_node_breaks_link(self, db: Session) -> None:
        _seed_audit_chain(db, 3)
        events = db.query(AuditEvent).order_by(AuditEvent.created_at.asc()).all()
        db.delete(events[1])  # remove a middle node
        db.flush()

        report = LedgerVerifier(db).verify_audit_chain()
        assert report.valid is False
        # The chain is only reachable up to the deletion point.
        assert any("reachable" in b["reason"] for b in report.broken_links)


class TestPGLChain:
    def test_pgl_chain_from_exec_verifies(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        events = db.query(PGLLedgerEvent).all()
        assert events, "exec should have produced PGL ledger events"
        cert_id = events[0].certificate_id

        report = LedgerVerifier(db).verify_pgl_chain(cert_id)
        assert report.valid is True
        assert report.total >= 1

    def test_pgl_tamper_detected(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        ev = db.query(PGLLedgerEvent).first()
        assert ev is not None
        ev.payload = {**ev.payload, "event_type": "forged"}
        db.flush()

        report = LedgerVerifier(db).verify_pgl_chain(ev.certificate_id)
        assert report.valid is False

    def test_verify_all_valid_after_exec(self, client: TestClient, db: Session) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        result = LedgerVerifier(db).verify_all()
        assert result["valid"] is True
        # Audit chain + at least one PGL chain present.
        assert len(result["chains"]) >= 2


class TestVerifyEndpoint:
    def test_verify_endpoint_ok(self, client: TestClient) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        resp = client.get("/v1/audit/verify")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    def test_verify_endpoint_detects_tamper(
        self, client: TestClient, db: Session
    ) -> None:
        client.post("/v1/exec", json={"prompt": "hello"})
        ev = db.query(AuditEvent).first()
        assert ev is not None
        ev.payload = {**ev.payload, "tampered": True}
        db.flush()

        resp = client.get("/v1/audit/verify")
        assert resp.status_code == 200
        assert resp.json()["valid"] is False
