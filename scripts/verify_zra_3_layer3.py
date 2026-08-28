import os
import sys
import traceback

sys.path.insert(0, os.path.abspath('.'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy
from cappo_backend.capability_mount.service import MountRegistry, MountScope, UnmountReason
from cappo_backend.db.base import Base
from cappo_backend.services.mount_pgl import AuditPGLAnchor


def run_zra3_layer3():
    print("Initializing ZRA-1 Layer 3 Proof...")
    
    _test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _TestSession = sessionmaker(bind=_test_engine, autoflush=False, expire_on_commit=False)
    
    Base.metadata.create_all(_test_engine)
    db = _TestSession()
    
    try:
        from cappo_backend.config import Settings
        settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
        svc = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))
        
        # 1. Acquire valid handles before termination
        pkg = CapabilityPackage(
            id="pkg_test_zra3_layer3@v1",
            family="test",
            title="Test ZRA 3",
            purpose="Layer 3 Handle Replay",
            reads=[],
            writes=["execution"]
        )
        svc.register_package(pkg)
        
        mount_record, anchor, error = svc.request_mount(
            package_ref="pkg_test_zra3_layer3@v1",
            scope=MountScope(workspace="ws1", project="proj1"),
            role="tester",
            policy=MountPolicy(
                require_human_approval_for_external_send=False,
                require_suppression_check=False
            ),
            ttl_seconds=600,
            execution_id="exec_zra3_layer3"
        )
        
        if mount_record is None:
            print("[CLASSIFICATION]: INVALID_TEST_FIXTURE")
            print(f"Failed to acquire handle: {error}")
            return
            
        handle = mount_record.binding
        mount_id = mount_record.mount.id
        
        # Verify handle is usable BEFORE termination
        try:
            handle.evaluate_pure("execution")
            print("Pre-termination handle valid and usable.")
        except Exception as e:
            print("[CLASSIFICATION]: INVALID_TEST_FIXTURE")
            print(f"Pre-termination evaluation failed: {e}")
            return

        # 2. Terminate the execution/lease
        print("Terminating lease...")
        decision, reason, term_anchor = svc.terminate(
            mount_id=mount_id,
            reason=UnmountReason.EXPLICIT_TERMINATE
        )
        
        if decision.value != "allow":
            print("[CLASSIFICATION]: INVALID_TEST_FIXTURE")
            print(f"Termination failed: {reason}")
            return
            
        print("Terminal state reached successfully.")
        
        # 3. Attempt to reuse the exact same handle instance without fresh authorization
        print("Attempting to reuse stale handle...")
        
        try:
            handle.evaluate_pure("execution")
            print("[CLASSIFICATION]: STALE_HANDLE_STILL_USABLE")
            print("ZRA-1 Layer 3 FAILED: Handle was still usable after global termination.")
        except Exception as e:
            if "terminated" in str(e).lower() or "expired" in str(e).lower() or "revoked" in str(e).lower():
                print("[CLASSIFICATION]: STALE_HANDLE_DENIED")
                print(f"ZRA-1 Layer 3 PASSED: Handle evaluation correctly denied: {e}")
            else:
                print("[CLASSIFICATION]: INVALID_TEST_FIXTURE")
                print(f"Unexpected exception during stale handle evaluation: {e}")
                traceback.print_exc()

    finally:
        db.close()
        Base.metadata.drop_all(_test_engine)

if __name__ == '__main__':
    run_zra3_layer3()
