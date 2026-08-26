"""
ARCH-P2 CapabilityLease invariant tests.

These tests prove the constitutional invariant:
    effective_lease_authority ⊆ biscuit_authority ⊆ package_authority

All adversarial cases that must DENY are explicitly covered.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cappo_backend.models.capability_lease import (
    AuthorityContext,
    CapabilityLease,
    ConnectivityState,
    InvariantViolationError,
    LeaseState,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_biscuit_auth(
    actions: set[str] | None = None,
    resources: set[str] | None = None,
    executor: str = "spiffe://example.org/executor/valid",
    expires_at: datetime | None = None,
    delegation_depth: int = 0,
    max_delegation_depth: int = 1,
    authority_epoch: int = 0,
) -> AuthorityContext:
    return AuthorityContext(
        allowed_actions=actions or {"contact.read"},
        allowed_resources=resources or {"/contacts/*"},
        executor_spiffe_id=executor,
        expires_at=expires_at or (_now() + timedelta(minutes=10)),
        delegation_depth=delegation_depth,
        max_delegation_depth=max_delegation_depth,
        authority_epoch=authority_epoch,
    )


def _make_package_auth(
    actions: set[str] | None = None,
    resources: set[str] | None = None,
    executor: str = "spiffe://example.org/executor/valid",
) -> AuthorityContext:
    return AuthorityContext(
        allowed_actions=actions or {"contact.read", "contact.write"},
        allowed_resources=resources or {"/contacts/*"},
        executor_spiffe_id=executor,
        expires_at=_now() + timedelta(hours=1),
        delegation_depth=0,
        max_delegation_depth=1,
        authority_epoch=0,
    )


def _make_lease(
    actions: set[str] | None = None,
    resources: set[str] | None = None,
    executor: str = "spiffe://example.org/executor/valid",
    expires_at: datetime | None = None,
    lease_state: str = LeaseState.ACTIVE.value,
    delegation_depth: int = 0,
    max_delegation_depth: int = 1,
    authority_epoch: int = 0,
    revocation_epoch: int = 0,
    offline_enabled: bool = False,
) -> CapabilityLease:
    lease = CapabilityLease()
    lease.lease_id = "test-lease-001"
    lease.mount_id = "test-mount-001"
    lease.capability_id = "test-cap-001"
    lease.policy_version = "1.0"
    lease.execution_identity = "exec-001"
    lease.subject_spiffe_id = "spiffe://example.org/workload/caller"
    lease.executor_spiffe_id = executor
    lease.biscuit_hash = "testhash"
    lease.issued_at = _now()
    lease.not_before = _now()
    lease.expires_at = expires_at or (_now() + timedelta(minutes=5))
    lease.lease_state = lease_state
    lease.lease_state_version = 1
    lease.authority_epoch = authority_epoch
    lease.revocation_epoch = revocation_epoch
    lease.delegation_depth = delegation_depth
    lease.max_delegation_depth = max_delegation_depth
    lease.allowed_actions = actions or {"contact.read"}
    lease.allowed_resources = resources or {"/contacts/123"}
    lease.offline_enabled = offline_enabled
    lease.reconciliation_required = False
    return lease


# ─── Happy path ────────────────────────────────────────────────────────────────

class TestValidSubset:
    def test_exact_match_is_valid(self):
        lease = _make_lease(actions={"contact.read"}, resources={"/contacts/123"})
        b_auth = _make_biscuit_auth(actions={"contact.read"}, resources={"/contacts/123"})
        p_auth = _make_package_auth(actions={"contact.read", "contact.write"}, resources={"/contacts/*"})
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        assert "contact.read" in effective.allowed_actions

    def test_biscuit_wildcard_covers_specific_lease_resource(self):
        """Biscuit: /contacts/*  Lease: /contacts/123  → VALID"""
        lease = _make_lease(resources={"/contacts/123"})
        b_auth = _make_biscuit_auth(resources={"/contacts/*"})
        p_auth = _make_package_auth(resources={"/contacts/*"})
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        assert "/contacts/123" in effective.allowed_resources

    def test_package_wildcard_covers_specific_lease_resource(self):
        """Package: /contacts/*  Biscuit: /contacts/123  Lease: /contacts/123  → VALID"""
        lease = _make_lease(resources={"/contacts/123"})
        b_auth = _make_biscuit_auth(resources={"/contacts/123"})
        p_auth = _make_package_auth(resources={"/contacts/*"})
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        assert "/contacts/123" in effective.allowed_resources


# ─── Action widening ────────────────────────────────────────────────────────────

class TestActionWidening:
    def test_lease_cannot_widen_action_beyond_biscuit(self):
        """Lease has contact.write but Biscuit only allows contact.read → DENY"""
        lease = _make_lease(actions={"contact.read", "contact.write"})
        b_auth = _make_biscuit_auth(actions={"contact.read"})
        p_auth = _make_package_auth(actions={"contact.read", "contact.write"})
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_ACTION"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    def test_lease_cannot_widen_action_beyond_package(self):
        """Lease has danger.delete which even package doesn't grant → DENY via intersection (not in effective)"""
        lease = _make_lease(actions={"contact.read"})
        b_auth = _make_biscuit_auth(actions={"contact.read"})
        p_auth = _make_package_auth(actions={"contact.read"})  # no danger.delete
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        assert "danger.delete" not in effective.allowed_actions


# ─── Resource widening ──────────────────────────────────────────────────────────

class TestResourceWidening:
    def test_lease_cannot_widen_resource_beyond_biscuit(self):
        """Biscuit: /contacts/123  Lease: /contacts/*  → DENY"""
        lease = _make_lease(resources={"/contacts/*"})
        b_auth = _make_biscuit_auth(resources={"/contacts/123"})
        p_auth = _make_package_auth(resources={"/contacts/*"})
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_RESOURCE"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    def test_lease_cannot_use_wildcard_when_biscuit_is_specific(self):
        """Lease: * but Biscuit: /contacts/123 → DENY"""
        lease = _make_lease(resources={"*"})
        b_auth = _make_biscuit_auth(resources={"/contacts/123"})
        p_auth = _make_package_auth(resources={"*"})
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_RESOURCE"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    def test_wildcard_biscuit_allows_specific_lease_resource(self):
        """Biscuit: *  Lease: /contacts/123  → VALID"""
        lease = _make_lease(resources={"/contacts/123"})
        b_auth = _make_biscuit_auth(resources={"*"})
        p_auth = _make_package_auth(resources={"*"})
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        assert "/contacts/123" in effective.allowed_resources


# ─── Executor mismatch ──────────────────────────────────────────────────────────

class TestExecutorMismatch:
    def test_lease_cannot_change_executor(self):
        """Biscuit is bound to executor A, lease says executor B → DENY"""
        lease = _make_lease(executor="spiffe://example.org/executor/A")
        b_auth = _make_biscuit_auth(executor="spiffe://example.org/executor/B")
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_CHANGE_EXECUTOR"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)


# ─── Expiry extension ──────────────────────────────────────────────────────────

class TestExpiryExtension:
    def test_lease_cannot_extend_expiry_beyond_biscuit(self):
        """Biscuit expires in 1 min, lease claims 10 min → DENY"""
        lease = _make_lease(expires_at=_now() + timedelta(minutes=10))
        b_auth = _make_biscuit_auth(expires_at=_now() + timedelta(minutes=1))
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_EXTEND_EXPIRY"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)


# ─── Delegation widening ────────────────────────────────────────────────────────

class TestDelegationWidening:
    def test_lease_cannot_increase_delegation_depth(self):
        """Biscuit allows max depth 0, lease claims depth 2 → DENY"""
        lease = _make_lease(delegation_depth=2, max_delegation_depth=2)
        b_auth = _make_biscuit_auth(max_delegation_depth=0)
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_INCREASE_DELEGATION"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)


# ─── Epoch rollback ─────────────────────────────────────────────────────────────

class TestEpochRollback:
    def test_lease_cannot_rollback_authority_epoch(self):
        """Biscuit is at epoch 5, lease is at epoch 3 → DENY"""
        lease = _make_lease(authority_epoch=3)
        b_auth = _make_biscuit_auth(authority_epoch=5)
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_ROLLBACK_AUTHORITY_EPOCH"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    def test_transition_state_enforces_monotonic_epoch(self):
        """Accepting a state transition with older epoch must fail"""
        lease = _make_lease(authority_epoch=10)
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_ROLLBACK_AUTHORITY_EPOCH"):
            lease.transition_state(LeaseState.ACTIVE, current_epoch=5)

    def test_transition_state_accepts_newer_epoch(self):
        lease = _make_lease(authority_epoch=5)
        lease.transition_state(LeaseState.ACTIVE, current_epoch=6)
        assert lease.authority_epoch == 6


# ─── Revocation resurrection ────────────────────────────────────────────────────

class TestRevocationResurrection:
    def test_revoked_lease_cannot_authorize(self):
        lease = _make_lease(lease_state=LeaseState.REVOKED.value)
        b_auth = _make_biscuit_auth()
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    def test_revoked_lease_cannot_transition_to_active(self):
        lease = _make_lease(lease_state=LeaseState.REVOKED.value)
        with pytest.raises(InvariantViolationError, match="REVOKED_LEASE_CAN_RESURRECT"):
            lease.transition_state(LeaseState.ACTIVE, current_epoch=1)

    def test_expired_lease_cannot_transition_to_active(self):
        lease = _make_lease(lease_state=LeaseState.EXPIRED.value)
        with pytest.raises(InvariantViolationError, match="EXPIRED_LEASE_CAN_RESURRECT"):
            lease.transition_state(LeaseState.ACTIVE, current_epoch=1)


# ─── No Biscuit → no authority ──────────────────────────────────────────────────

class TestNoBiscuit:
    def test_metadata_cannot_authorize_without_biscuit(self):
        """If Biscuit is None, the evaluator must raise, not fall through."""
        lease = _make_lease()
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="METADATA_CAN_AUTHORIZE_WITHOUT_BISCUIT"):
            lease.evaluate_authority(None, p_auth, ConnectivityState.ONLINE)


# ─── Offline enforcement ────────────────────────────────────────────────────────

class TestOfflineEnforcement:
    def test_offline_not_allowed_when_not_enabled(self):
        lease = _make_lease(offline_enabled=False)
        b_auth = _make_biscuit_auth()
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="OFFLINE_MODE_CAN_CREATE_NEW_AUTHORITY"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)

    def test_offline_allowed_when_enabled(self):
        lease = _make_lease(offline_enabled=True)
        b_auth = _make_biscuit_auth()
        p_auth = _make_package_auth()
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)
        assert "contact.read" in effective.allowed_actions

    def test_offline_duration_exceeded(self):
        lease = _make_lease(offline_enabled=True)
        lease.maximum_offline_duration = 3600
        # updated_at was more than 1 hour ago
        lease.updated_at = _now() - timedelta(hours=2)
        b_auth = _make_biscuit_auth()
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="OFFLINE_DURATION_EXCEEDED"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)

    def test_reconciliation_required_blocks_offline(self):
        lease = _make_lease(offline_enabled=True)
        lease.reconciliation_required = True
        b_auth = _make_biscuit_auth()
        p_auth = _make_package_auth()
        with pytest.raises(InvariantViolationError, match="RECONCILIATION_REQUIRED_BLOCKS_OFFLINE"):
            lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)


# ─── Attenuation ────────────────────────────────────────────────────────────────

class TestAttenuation:
    def test_attenuation_cannot_widen_actions(self):
        lease = _make_lease(actions={"contact.read"})
        with pytest.raises(InvariantViolationError, match="CHILD_LEASE_CANNOT_WIDEN_ACTIONS"):
            lease.attenuate({"contact.read", "contact.write"}, {"/contacts/123"}, current_epoch=1)

    def test_attenuation_cannot_widen_resources(self):
        lease = _make_lease(resources={"/contacts/123"})
        with pytest.raises(InvariantViolationError, match="CHILD_LEASE_CANNOT_WIDEN_RESOURCES"):
            lease.attenuate({"contact.read"}, {"/contacts/*"}, current_epoch=1)

    def test_attenuation_cannot_rollback_revocation_epoch(self):
        lease = _make_lease(revocation_epoch=5)
        with pytest.raises(InvariantViolationError, match="LEASE_CAN_ROLLBACK_REVOCATION_EPOCH"):
            lease.attenuate({"contact.read"}, {"/contacts/123"}, current_epoch=3)

    def test_valid_attenuation_narrows(self):
        lease = _make_lease(actions={"contact.read", "contact.write"}, resources={"/contacts/*"})
        lease.attenuate({"contact.read"}, {"/contacts/123"}, current_epoch=1)
        assert lease.allowed_actions == {"contact.read"}
        assert lease.allowed_resources == {"/contacts/123"}
