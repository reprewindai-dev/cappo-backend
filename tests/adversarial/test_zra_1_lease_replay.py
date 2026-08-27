"""
ZRA-1 — CapabilityLease Replay Vulnerability Falsifier

Hypothesis: Terminating a capability mount must also terminate the associated
CapabilityLease, rendering it unusable for offline or decentralized evaluation.

Falsifier: If the CapabilityLease remains ACTIVE in the database after `terminate()`
is called, the system fails ZRA-1, because downstream systems relying on lease state
will incorrectly allow execution.
"""

import pytest
from sqlalchemy.orm import Session
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.capability_mount.models import MountScope, MountPolicy, Decision, UnmountReason
from cappo_backend.services.mount_pgl import AuditPGLAnchor
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState

CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/zra1-agent"
CAPABILITY_ID  = "test.resource@v1"
ACTION         = "resource.read"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_zra_1_lease_remains_active_after_termination(db: Session):
    """
    Attempt to query the lease state after the mount has been terminated.
    This test is expected to fail initially, proving the vulnerability exists.
    """
    reg = _build_registry(db)
    
    # 0. Register package
    from cappo_backend.capability_mount.models import CapabilityPackage
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="ZRA-1 lease replay test",
            reads=[ACTION],
            writes=[],
        )
    )

    # 1. Setup a valid mount
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="zra1-exec-002",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None
    mount_id = mount_record.mount.id
    lease_id = f"lease_{mount_id}"

    # Verify lease is ACTIVE before termination
    lease_before = db.query(CapabilityLease).filter_by(lease_id=lease_id).first()
    assert lease_before is not None
    assert lease_before.lease_state == LeaseState.ACTIVE.value

    # 2. Terminate the capability
    reg.terminate(mount_id=mount_id, reason=UnmountReason.TOKEN_EXPIRY)

    # 3. Query the lease state again
    db.expire_all() # Ensure we read fresh state
    lease_after = db.query(CapabilityLease).filter_by(lease_id=lease_id).first()
    assert lease_after is not None
    
    # Falsifier: The lease should NOT be ACTIVE after termination.
    # We assert that it is NOT active. If the code is broken, this assertion will fail,
    # correctly recording the ZRA-1 failure state.
    assert lease_after.lease_state != LeaseState.ACTIVE.value, (
        "ZRA-1 Falsified: CapabilityLease remained ACTIVE after termination."
    )
