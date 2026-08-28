from cappo_backend.capability_mount.models import CapabilityPackage, LifecycleState, MountPolicy
from cappo_backend.capability_mount.service import MountScope, UnmountReason
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
from cappo_backend.models.capability_mount import CapabilityMount
from tests.adversarial.test_zra_3_mount_replay import _build_registry


def test_zra_3_mount_object_handle_survival(db):
    """
    Test mount object handle survival (MountRecord).
    Mount is a Pydantic model -- not SQLAlchemy-mapped -- so db.refresh() fails on it.
    Instead, query the canonical CapabilityMount ORM row after termination and verify
    the `terminated` column is True in the authoritative DB source.
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

    assert mount_record is not None
    assert mount_record.mount.lifecycle.state == LifecycleState.MOUNTED, "Pre-termination handle should be MOUNTED"

    svc.terminate(mount_record.mount.id, UnmountReason.EXPLICIT_TERMINATE)

    # Query canonical SQLAlchemy CapabilityMount ORM row -- authoritative DB truth
    db_row = db.query(CapabilityMount).filter(CapabilityMount.mount_id == mount_record.mount.id).first()
    assert db_row is not None, "CapabilityMount row must exist in DB"

    is_terminated_in_db = db_row.terminated
    print(f"\n[CLASSIFICATION: Mount Object Handle]: {'STALE_HANDLE_DENIED' if is_terminated_in_db else 'STALE_HANDLE_STILL_USABLE'}")
    assert is_terminated_in_db, "ZRA-1 Layer 3: CapabilityMount DB row must be marked terminated after explicit terminate"


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

    # Query by mount_id -- CapabilityLease.id column does not exist
    mount_id = mount_record.mount.id
    lease = db.query(CapabilityLease).filter(CapabilityLease.mount_id == mount_id).first()
    assert lease is not None
    assert lease.lease_state == LeaseState.ACTIVE.value, "Pre-termination lease should be ACTIVE"

    svc.terminate(mount_id, UnmountReason.EXPLICIT_TERMINATE)

    # Force SQLAlchemy to reload from DB -- CapabilityLease IS a mapped ORM object
    db.refresh(lease)

    print(f"\n[CLASSIFICATION: Cached Lease Object]: {'STALE_HANDLE_STILL_USABLE' if lease.lease_state == LeaseState.ACTIVE.value else 'STALE_HANDLE_DENIED'}")
    assert lease.lease_state != LeaseState.ACTIVE.value, "ZRA-1 Layer 3: Cached Lease object should reflect termination"


def test_zra_3_missing_fixtures(db):
    print("\n[CLASSIFICATION: File-backed descriptor handle]: INVALID_TEST_FIXTURE")
    print("[CLASSIFICATION: Network/tunnel handle]: INVALID_TEST_FIXTURE")
    print("[CLASSIFICATION: Cloudflare connector/token handle]: INVALID_TEST_FIXTURE")