
import pytest

from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy
from cappo_backend.capability_mount.service import MountRegistry, MountScope, UnmountReason
from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
from cappo_backend.services.mount_pgl import AuditPGLAnchor


def _build_registry(db) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_zra_3_stale_handle_replay(db):
    """
    ZRA-1 Layer 3: Handle Replay Falsifier.
    Proves whether a caller holding a valid ExecutionBinding handle can continue 
    to execute actions after the mount/lease is terminated globally.
    """
    svc = _build_registry(db)
    
    pkg = CapabilityPackage(
        id="pkg_test_zra3@v1",
        family="test",
        title="Test ZRA 3",
        purpose="Layer 3 Handle Replay",
        reads=[],
        writes=["execution"]
    )
    svc.register_package(pkg)
    
    mount_record, anchor, error = svc.request_mount(
        package_ref="pkg_test_zra3@v1",
        scope=MountScope(workspace="ws1", project="proj1"),
        role="tester",
        policy=MountPolicy(
            require_human_approval_for_external_send=False,
            require_suppression_check=False
        ),
        ttl_seconds=600,
        execution_id="exec_zra3"
    )
    assert mount_record is not None, f"Failed to get mount handle: {error}"
    
    handle = mount_record.binding
    mount_id = mount_record.mount.id
    
    handle.evaluate_pure("execution")
    
    decision, reason, term_anchor = svc.terminate(
        mount_id=mount_id,
        reason=UnmountReason.EXPLICIT_TERMINATE
    )
    assert decision.value == "allow"
    assert reason == "terminated"
    
    lease = db.query(CapabilityLease).filter_by(mount_id=mount_id).first()
    assert lease.lease_state == LeaseState.REVOKED.value
    
    try:
        handle.evaluate_pure("execution")
        
        pytest.fail("ZRA-1 Falsified: Stale ExecutionBinding handle remained usable after global termination.")
    except Exception as e:
        if "falsified" in str(e).lower():
            raise
        assert "terminated" in str(e).lower() or "expired" in str(e).lower()

def test_zra_3_multiple_handle_invalidation(db):
    """
    Test that terminating a common authority invalidates multiple derived bindings.
    """
    svc = _build_registry(db)
    pkg = CapabilityPackage(
        id="pkg_multi@v1",
        family="test",
        title="Multi",
        purpose="Multi",
        reads=[],
        writes=["execution"]
    )
    svc.register_package(pkg)
    
    mount_record_a, _, _ = svc.request_mount(
        package_ref="pkg_multi@v1",
        scope=MountScope(workspace="ws1", project="proj1"),
        role="tester",
        policy=MountPolicy(require_human_approval_for_external_send=False, require_suppression_check=False),
        ttl_seconds=600
    )
    handle_a = mount_record_a.binding
    mount_id = mount_record_a.mount.id
    
    # Construct a second handle via reconstruction
    mount_record_b, state = svc.status(mount_id)
    handle_b = mount_record_b.binding
    
    # Both should work
    handle_a.evaluate_pure("execution")
    handle_b.evaluate_pure("execution")
    
    # Terminate common authority
    svc.terminate(mount_id=mount_id, reason=UnmountReason.EXPLICIT_TERMINATE)
    
    with pytest.raises(Exception) as e_a:
        handle_a.evaluate_pure("execution")
    assert "terminated" in str(e_a.value).lower()
    
    with pytest.raises(Exception) as e_b:
        handle_b.evaluate_pure("execution")
    assert "terminated" in str(e_b.value).lower()

def test_zra_3_post_termination_reissuance(db):
    """
    Test that re-requesting/reconstructing an already terminated handle fails closed immediately.
    """
    svc = _build_registry(db)
    pkg = CapabilityPackage(
        id="pkg_reissue@v1",
        family="test",
        title="Reissue",
        purpose="Reissue",
        reads=[],
        writes=["execution"]
    )
    svc.register_package(pkg)
    
    mount_record_a, _, _ = svc.request_mount(
        package_ref="pkg_reissue@v1",
        scope=MountScope(workspace="ws1", project="proj1"),
        role="tester",
        policy=MountPolicy(require_human_approval_for_external_send=False, require_suppression_check=False),
        ttl_seconds=600
    )
    mount_id = mount_record_a.mount.id
    
    svc.terminate(mount_id=mount_id, reason=UnmountReason.EXPLICIT_TERMINATE)
    
    # Attempt reconstruction
    reconstructed_record = svc.get(mount_id)
    handle_new = reconstructed_record.binding
    
    with pytest.raises(Exception) as e:
        handle_new.evaluate_pure("execution")
    assert "terminated" in str(e.value).lower()
