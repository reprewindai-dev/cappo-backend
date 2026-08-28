from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy
from cappo_backend.capability_mount.service import MountScope, UnmountReason
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
from tests.adversarial.test_zra_3_mount_replay import _build_registry


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
    lease = db.query(CapabilityLease).filter(CapabilityLease.mount_id == mount_id).first()
    assert lease is not None
    assert lease.lease_state == LeaseState.ACTIVE.value, "Pre-termination lease should be ACTIVE"
    
    # 2. Terminate execution/lease
    svc.terminate(mount_id, UnmountReason.EXPLICIT_TERMINATE)
    
    # 3. Reuse exact same handle instance (without refreshing from DB)
    print(f"\n[CLASSIFICATION: Cached Lease Object]: {'STALE_HANDLE_STILL_USABLE' if lease.lease_state == LeaseState.ACTIVE.value else 'STALE_HANDLE_DENIED'}")
    
    # SQLAlchemy objects might stay stale unless refreshed. We assert it shouldn't.
    assert lease.lease_state != LeaseState.ACTIVE.value, "ZRA-1 Layer 3: Cached Lease object should reflect termination"
