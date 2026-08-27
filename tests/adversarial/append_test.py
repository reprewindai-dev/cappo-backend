
def test_zra_2_transaction_rollback_partial_transition(db: Session, monkeypatch):
    from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
    from cappo_backend.capability_mount.models import CapabilityMount
    reg = _build_registry(db)
    
    mount_record, _, _ = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="zra1-exec-004",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    mount_id = mount_record.mount.id

    original_transition = CapabilityLease.transition_state
    def _faulty_transition(*args, **kwargs):
        raise ValueError("Simulated database failure during transition")
    
    monkeypatch.setattr(CapabilityLease, "transition_state", _faulty_transition)
    
    with pytest.raises(ValueError, match="Simulated database failure"):
        reg.terminate(mount_id=mount_id, reason=UnmountReason.TOKEN_EXPIRY)
        
    db.rollback()
    
    row = db.query(CapabilityMount).filter_by(mount_id=mount_id).first()
    assert row.terminated is False, "Mount termination should rollback if lease transition fails"
    
    lease = db.query(CapabilityLease).filter_by(lease_id=f"lease_{mount_id}").first()
    assert lease.lease_state == LeaseState.ACTIVE.value, "Lease must remain ACTIVE if mount termination fails"

def test_zra_2_disconnected_token_crosses_expiry(db: Session, monkeypatch):
    from cappo_backend.security.biscuit import mint_biscuit_capability
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION],
        writes=[],
        execution_id="zra1-exec-005",
        ttl_seconds=-1, # Already expired
    )
    
    disconnected_offline_valid_after = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*"
    )
    assert disconnected_offline_valid_after is False, "Disconnected verifier must reject expired token"

def test_zra_2_stale_epoch_cannot_overwrite_newer_locally_known(db: Session):
    from cappo_backend.security.biscuit import mint_biscuit_capability
    scope = "execution:zra1-exec-006"
    
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION],
        writes=[],
        execution_id="zra1-exec-006",
        ttl_seconds=300,
        revocation_scope=scope,
        revocation_epoch=1,
    )
    
    local_known_epoch = 5
    stale_sync_epoch = 0
    merged_epoch = max(local_known_epoch, stale_sync_epoch)
    
    valid = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*",
        known_epochs={scope: merged_epoch}
    )
    
    assert valid is False, "Verifier must reject token if merged epoch is greater than token epoch"
