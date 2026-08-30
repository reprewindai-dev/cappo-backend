def test_debug(db):
    from cappo_backend.capability_mount.models import MountPolicy, MountScope
    from cappo_backend.capability_mount.service import MountRegistry
    
    reg = MountRegistry(db)
    mount_record, anchor, reason = reg.request_mount(
        package_ref="echo@v1",
        scope=MountScope(workspace="ws_1", project="prj_1", reads=["echo"], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=1, 
        owner_principal="auth-disabled",
        execution_id="exec_123",
        caller_spiffe_id="spiffe://example.org/workload/cappo-backend",
        executor_spiffe_id="spiffe://example.org/workload/my-agent",
    )
    print("REASON FOR FAILURE:", reason)
    assert False
