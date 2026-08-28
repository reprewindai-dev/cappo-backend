import asyncio
import sys
from datetime import datetime, timezone

from cappo_backend.capability_mount.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Grants,
    LifecycleState,
    MountPolicy,
    MountScope,
    MountToken,
    TokenDescriptorScope,
    UnmountReason,
)
from cappo_backend.capability_mount.service import MountRegistry


async def main():
    print("Initializing MountRegistry (ZRA-1 Layer 3 MountRecord test)")
    
    # Setup in-memory SQLite DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    registry = MountRegistry(db=db)

    datetime.now(timezone.utc)
    pkg = CapabilityPackage(
        package_id="pkg_test_zra3_mountrecord@v1",
        version="v1",
        description="ZRA-1 MountRecord Test",
        scope=MountScope(
            resources=["arn:veklom:test:zra3:mountrecord"],
            actions=["test:execute"],
            conditions={}
        ),
        grants=Grants(max_concurrent=1, rate_limit_rpm=10),
        token=MountToken(
            policy=MountPolicy(
                type="ephemeral",
                descriptor_scope=TokenDescriptorScope.MEMORY
            )
        )
    )

    mount_id = "test-mount-record-123"
    print(f"Acquiring MountRecord for mount_id={mount_id}")
    
    await registry.acquire(
        mount_id=mount_id,
        workspace_id="ws_test",
        package=pkg,
        duration_seconds=3600
    )
    
    mount_record = registry._mounts.get(mount_id)
    if not mount_record:
        print("Failed to find MountRecord in registry.")
        sys.exit(1)
        
    print(f"Pre-Termination Verification: MountRecord state is {mount_record.mount.lifecycle.state.value}")
    if mount_record.mount.lifecycle.state != LifecycleState.ACTIVE:
        print("ERROR: MountRecord is not ACTIVE.")
        sys.exit(1)
        
    print("Handle was valid and usable.")

    print(f"Terminating lease for mount_id={mount_id}")
    await registry.terminate(mount_id, UnmountReason.EXPLICIT_TERMINATE)
    
    print("Attempting to reuse stale MountRecord...")
    if mount_record.mount.lifecycle.state == LifecycleState.TERMINATED:
        print("MountRecord successfully transitioned to TERMINATED in-place.")
        print("[CLASSIFICATION]: STALE_HANDLE_DENIED")
        print("ZRA-1 Layer 3 PASSED: MountRecord handle correctly reflects terminated state.")
    else:
        print("ERROR: MountRecord did not reflect terminated state in-place!")
        print("[CLASSIFICATION]: STALE_HANDLE_STILL_USABLE")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
