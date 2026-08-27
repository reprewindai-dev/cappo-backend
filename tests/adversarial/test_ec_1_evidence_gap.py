"""
EC-1 — Evidence Coverage

Hypothesis: Every governed consequence or governed data access inside the declared 
observation boundary either produces the required evidence for its configured evidence 
profile or creates an explicit unreconciled discrepancy.

Attack program:
Introduce test harnesses that deliberately execute a consequential action while 
sabotaging, bypassing, or misconfiguring receipt generation (e.g., crashing the 
evidence writer, simulating disk full, dropping network to the local log store).
Compare consequence sinks (e.g., DB mutations, queue messages) against evidence stores 
(COSE receipts, Merkle tree, SCITT logs) to detect gaps.

Falsifier: any successful consequence with no corresponding evidence trail that is not 
surfaced as an unreconciled discrepancy.
"""

import pytest
from sqlalchemy.orm import Session
from unittest import mock
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.capability_mount.models import MountScope, MountPolicy, Decision
from cappo_backend.services.mount_pgl import AuditPGLAnchor

CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/ec1-agent"
CAPABILITY_ID  = "test.resource@v1"
ACTION         = "resource.write"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_ec_1_evidence_writer_crash(db: Session):
    """
    Test that if the evidence writer crashes, the consequence is rolled back 
    or surface an explicit discrepancy (decision != ALLOW).
    """
    reg = _build_registry(db)
    
    # 1. Register package and setup a valid mount
    from cappo_backend.capability_mount.models import CapabilityPackage
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="EC-1 test capability",
            reads=[],
            writes=[ACTION],
        )
    )
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[], writes=[ACTION]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="ec1-exec-001",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce
    
    # Mock the internal function that writes evidence to throw an Exception
    # (Simulating disk full or network drop to local log store)
    with mock.patch("cappo_backend.security.evidence.mint_signed_execution_evidence", side_effect=OSError("Disk Full")):
        with pytest.raises(OSError, match="Disk Full"):
            decision, reason, _, _ = reg.evaluate(
                mount_id=mount_id,
                action=ACTION,
                token_id=token_id,
                nonce=nonce,
                owner_principal="auth-disabled",
                spiffe_fields={
                    "caller_spiffe_id": CALLER_SPIFFE,
                    "executor_spiffe_id": EXECUTOR_SPIFFE,
                    "caller_cert_sha256": "abcd" * 16,
                    "trust_domain": "example.org",
                },
            )
            # Falsifier: If this block completes without raising and returns ALLOW, we have an unevidenced consequence.
            assert decision != Decision.ALLOW, "Consequence allowed without evidence!"
