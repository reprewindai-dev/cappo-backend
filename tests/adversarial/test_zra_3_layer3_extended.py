import pytest
from datetime import datetime, timezone
from cappo_backend.capability_mount.service import MountRegistry, MountScope, UnmountReason
from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy, LifecycleState
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
from cappo_backend.services.mount_pgl import AuditPGLAnchor
from tests.adversarial.test_zra_3_mount_replay import _build_registry

def test_zra_3_mount_object_handle_survival(db):
    """
    Test mount object handle survival (MountRecord).
    """
    svc = _build_registry(db)
    pkg = CapabilityPackage(
        id="pkg_test_zra3_mount@v1",
        family="test",
        title="Test ZRA 3 Mount",
        purpose="Layer 3 Mount Handle Replay",
        reads=[],
        writes=["execution"]
    )
    svc.register_package(pkg)
    
    mount_record, anchor, error = svc.request_mount(
        package_ref="pkg_test_zra3_mount@v1",
        scope=MountScope(workspace="ws1", project="proj1"),
        role="tester",
        policy=MountPolicy(require_human_approval_for_external_send=False, require_suppression_check=False),
        ttl_seconds=600,
        execution_id="exec_zra3"
    )
    
    # 1. Acquire valid handle
    assert mount_record is not None
    assert mount_record.mount.lifecycle.state == LifecycleState.MOUNTED, "Pre-termination handle should be MOUNTED"
    
    # 2. Terminate execution/lease
    svc.terminate(mount_record.mount.id, UnmountReason.EXPLICIT_TERMINATE)
    
    # 3. Reuse exact same handle instance
    # If the handle instance wasn't updated, it would still say MOUNTED.
    is_active_after = (mount_record.mount.lifecycle.state == LifecycleState.MOUNTED)
    
    print(f"\n[CLASSIFICATION: Mount Object Handle]: {'STALE_HANDLE_STILL_USABLE' if is_active_after else 'STALE_HANDLE_DENIED'}")
    assert not is_active_after, "ZRA-1 Layer 3: MountRecord should reflect termination"

def test_zra_3_cached_lease_object_survival(db):
    """
    Test cached capability/lease object survival.
    """
    svc = _build_registry(db)
    pkg = CapabilityPackage(
        id="pkg_test_zra3_lease@v1",
        family="test",
        title="Test ZRA 3 Lease",
        purpose="Layer 3 Lease Handle Replay",
        reads=[],
        writes=["execution"]
    )
    svc.register_package(pkg)
    
    mount_record, anchor, error = svc.request_mount(
        package_ref="pkg_test_zra3_lease@v1",
        scope=MountScope(workspace="ws1", project="proj1"),
        role="tester",
        policy=MountPolicy(require_human_approval_for_external_send=False, require_suppression_check=False),
        ttl_seconds=600,
        execution_id="exec_zra3"
    )
    
    # 1. Acquire valid handle (db object)
    mount_id = mount_record.mount.id
    lease = db.query(CapabilityLease).filter(CapabilityLease.id == mount_id).first()
    assert lease is not None
    assert lease.state == LeaseState.ACTIVE, "Pre-termination lease should be ACTIVE"
    
    # 2. Terminate execution/lease
    svc.terminate(mount_id, UnmountReason.EXPLICIT_TERMINATE)
    
    # 3. Reuse exact same handle instance (without refreshing from DB)
    print(f"\n[CLASSIFICATION: Cached Lease Object]: {'STALE_HANDLE_STILL_USABLE' if lease.state == LeaseState.ACTIVE else 'STALE_HANDLE_DENIED'}")
    
    # SQLAlchemy objects might stay stale unless refreshed. We assert it shouldn't.
    assert lease.state != LeaseState.ACTIVE, "ZRA-1 Layer 3: Cached Lease object should reflect termination"

def test_zra_3_missing_fixtures(db):
    print("\n[CLASSIFICATION: File-backed descriptor handle]: INVALID_TEST_FIXTURE")
    print("[CLASSIFICATION: Network/tunnel handle]: INVALID_TEST_FIXTURE")
    print("[CLASSIFICATION: Cloudflare connector/token handle]: INVALID_TEST_FIXTURE")
