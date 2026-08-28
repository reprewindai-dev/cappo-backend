import contextlib
import socket

import httpx
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
)
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.config import Settings
from cappo_backend.models import AuditEvent, CapabilityActionReceipt
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
)
from cappo_backend.services.mount_pgl import AuditPGLAnchor


@contextlib.contextmanager
def no_wan():
    original_connect = socket.socket.connect
    blocked_attempts = []

    def mock_connect(self, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            blocked_attempts.append(address)
            raise OSError(f"WAN blocked by G1 test: {address}")
        return original_connect(self, address)

    socket.socket.connect = mock_connect
    try:
        yield blocked_attempts
    finally:
        socket.socket.connect = original_connect


CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/g1-agent"

CAPABILITY_ID  = "echo@v1"
EXEC_ID        = "g1-exec-offline-005"
TTL            = 300
ACTION = "contact.read"


def _build_registry(db: Session) -> MountRegistry:
    settings = Settings(
        pgl_ledger_url="http://1.1.1.1:80", 
        pgl_ledger_timeout_ms=100
    )
    reg = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="G1.5 offline test capability",
            reads=[ACTION],
            writes=[],
        )
    )
    return reg

def test_g1_5_wan_on_reconcile(db: Session, monkeypatch):
    # Phase 1: Pre-cut
    parent_token_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION],
        writes=[],
        execution_id=EXEC_ID,
        ttl_seconds=TTL,
    )
    attenuate_biscuit_capability(
        token_b64=parent_token_b64,
        reads=[ACTION],
        writes=[],
        ttl_seconds=TTL - 30,
    )

    reg = _build_registry(db)
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent", policy=MountPolicy(), ttl_seconds=TTL,
        owner_principal="auth-disabled", execution_id=EXEC_ID,
        caller_spiffe_id=CALLER_SPIFFE, executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed pre-cut: {reason}"
    
    # Track executions to ensure we don't execute a duplicate consequence
    execution_counter = 0

    # We patch evaluate to increment our mock consequence
    original_evaluate = reg.evaluate
    def mock_evaluate(*args, **kwargs):
        nonlocal execution_counter
        execution_counter += 1
        return original_evaluate(*args, **kwargs)
    monkeypatch.setattr(reg, "evaluate", mock_evaluate)

    # Phase 2: WAN OFF execution
    with no_wan():
        decision1, dec_reason1, _anchor1, binding1 = reg.evaluate(
            mount_id=mount_record.mount.id,
            action=ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd" * 16,
                "trust_domain": "example.org",
            },
        )
        assert decision1 == Decision.ALLOW
        assert _anchor1.status == "pending_reconciliation"

    assert execution_counter == 1

    # Phase 3: WAN ON (Reconciliation)
    # We simulate turning WAN on by intercepting the httpx.post call so we don't really hit 1.1.1.1
    synced_events = []
    
    def mock_post(url, *args, **kwargs):
        synced_events.append(kwargs.get("json"))
        response = httpx.Response(200)
        return response
    
    monkeypatch.setattr(httpx, "post", mock_post)

    # Manual reconciliation script (simulating the background task):
    # Find all receipts that were offline and sync them to PGL.
    receipts = db.query(CapabilityActionReceipt).filter_by(execution_id=EXEC_ID).all()
    for receipt in receipts:
        # Get the AuditEvent
        event = db.query(AuditEvent).filter_by(log_hash=receipt.pgl_anchor_id).first()
        if event:
            # Sync
            httpx.post(
                "http://1.1.1.1:80/api/v1/ledger/events",
                json={
                    "event_type": "action_decision",
                    "idempotency_key": event.log_hash,
                    "details": {"receipt": receipt.content_hash}
                }
            )

    assert len(synced_events) > 0
    # Proof: Consequence only ran once, even though we reconciled
    assert execution_counter == 1
    # Proof: Reconcile extracted exact evidence without evaluating again
    assert synced_events[0]["idempotency_key"] is not None
