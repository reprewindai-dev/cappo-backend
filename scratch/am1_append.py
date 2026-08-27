def test_am_1_mount_scope_widening(db: Session):
    from cappo_backend.capability_mount.service import MountRegistry
    from cappo_backend.capability_mount.models import CapabilityPackage, MountScope, MountPolicy
    reg = MountRegistry(db)
    
    # Register package with only READ
    reg.register_package(
        CapabilityPackage(
            id="test.am1.package@v1",
            family="test",
            title="Test Package",
            purpose="AM-1 testing",
            reads=["test.read"],
            writes=[],
        )
    )
    
    # Try to mount it and maliciously ask for WRITE scope
    mount_record, anchor, reason = reg.request_mount(
        package_ref="test.am1.package@v1",
        scope=MountScope(workspace="ws", project="pj", reads=["test.read"], writes=["test.write"]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="am1-exec-002",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    
    assert mount_record is None, "AM-1 Falsified: MountRegistry allowed mount scope to exceed package scope!"
    assert "scope exceeds package limits" in reason or "not declared in package" in reason or "invalid" in reason.lower()
