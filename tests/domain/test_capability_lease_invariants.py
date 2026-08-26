import pytest
from datetime import datetime, timedelta, timezone
from cappo_backend.models.capability_lease import (
    CapabilityLease, 
    LeaseState, 
    ConnectivityState, 
    AuthorityContext, 
    InvariantViolationError
)

def create_valid_lease() -> CapabilityLease:
    now = datetime.now(timezone.utc)
    return CapabilityLease(
        lease_id="lease_123",
        mount_id="mount_123",
        capability_id="cap_123",
        policy_version="v1",
        execution_identity="ei_123",
        subject_spiffe_id="spiffe://veklom/user",
        executor_spiffe_id="spiffe://veklom/workload",
        biscuit_hash="hash",
        issued_at=now,
        not_before=now,
        expires_at=now + timedelta(hours=1),
        lease_state=LeaseState.ACTIVE,
        lease_state_version=1,
        authority_epoch=10,
        revocation_epoch=5,
        delegation_depth=0,
        max_delegation_depth=2,
        allowed_actions={"payment.read"},
        allowed_resources={"/payments/123"},
        contextual_bounds={},
        offline_enabled=True,
        maximum_offline_duration=3600,
        offline_budget=10,
        offline_side_effect_limit=5,
        last_known_policy_epoch=10,
        last_known_revocation_epoch=5,
        reconciliation_required=True
    )

def create_biscuit_auth() -> AuthorityContext:
    now = datetime.now(timezone.utc)
    return AuthorityContext(
        allowed_actions={"payment.read"},
        allowed_resources={"/payments/123"},
        executor_spiffe_id="spiffe://veklom/workload",
        expires_at=now + timedelta(hours=2),
        delegation_depth=0,
        max_delegation_depth=2,
        authority_epoch=10
    )

def create_package_auth() -> AuthorityContext:
    now = datetime.now(timezone.utc)
    return AuthorityContext(
        allowed_actions={"payment.read", "payment.execute"},
        allowed_resources={"/payments/123", "/payments/456"},
        executor_spiffe_id="spiffe://veklom/workload",
        expires_at=now + timedelta(hours=10),
        delegation_depth=0,
        max_delegation_depth=5,
        authority_epoch=10
    )


def test_valid_authority_evaluation():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    effective = lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)
    
    assert effective.allowed_actions == {"payment.read"}
    assert effective.allowed_resources == {"/payments/123"}

def test_lease_can_widen_action():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    # DB says payment.execute, but biscuit says payment.read only
    lease.allowed_actions = {"payment.execute"}
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_ACTION"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_widen_resource():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    lease.allowed_resources = {"/payments/999"}
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_RESOURCE"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_extend_expiry():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    now = datetime.now(timezone.utc)
    biscuit.expires_at = now + timedelta(hours=1)
    lease.expires_at = now + timedelta(hours=2)
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_EXTEND_EXPIRY"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_change_executor():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    lease.executor_spiffe_id = "spiffe://veklom/rogue"
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_CHANGE_EXECUTOR"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_increase_delegation():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    lease.delegation_depth = 3
    biscuit.max_delegation_depth = 2
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_INCREASE_DELEGATION"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_rollback_authority_epoch():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    lease.authority_epoch = 9
    biscuit.authority_epoch = 10
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_ROLLBACK_AUTHORITY_EPOCH"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.ONLINE)

def test_lease_can_rollback_revocation_epoch():
    lease = create_valid_lease()
    
    # Try to attenuate and rollback epoch
    with pytest.raises(InvariantViolationError, match="LEASE_CAN_ROLLBACK_REVOCATION_EPOCH"):
        lease.attenuate({"payment.read"}, {"/payments/123"}, current_epoch=4) # lease has revocation_epoch 5

def test_revoked_lease_can_resurrect():
    lease = create_valid_lease()
    lease.transition_state(LeaseState.REVOKED, 5)
    
    with pytest.raises(InvariantViolationError, match="REVOKED_LEASE_CAN_RESURRECT"):
        lease.transition_state(LeaseState.ACTIVE, 6)

def test_offline_mode_can_create_new_authority():
    lease = create_valid_lease()
    biscuit = create_biscuit_auth()
    package = create_package_auth()
    
    lease.offline_enabled = False
    with pytest.raises(InvariantViolationError, match="OFFLINE_MODE_CAN_CREATE_NEW_AUTHORITY"):
        lease.evaluate_authority(biscuit, package, ConnectivityState.OFFLINE)

def test_metadata_can_authorize_without_biscuit():
    lease = create_valid_lease()
    package = create_package_auth()
    
    with pytest.raises(InvariantViolationError, match="METADATA_CAN_AUTHORIZE_WITHOUT_BISCUIT"):
        lease.evaluate_authority(None, package, ConnectivityState.ONLINE)

def test_child_lease_cannot_widen_authority():
    lease = create_valid_lease()
    
    with pytest.raises(InvariantViolationError, match="CHILD_LEASE_CANNOT_WIDEN_ACTIONS"):
        lease.attenuate({"payment.read", "payment.execute"}, {"/payments/123"}, 6)
        
    with pytest.raises(InvariantViolationError, match="CHILD_LEASE_CANNOT_WIDEN_RESOURCES"):
        lease.attenuate({"payment.read"}, {"/payments/123", "/payments/456"}, 6)
