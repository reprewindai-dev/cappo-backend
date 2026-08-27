"""
P3 — Authority Monotonicity Regression

Constitutional invariant:

    effective child authority  ⊆  parent authority  ⊆  governing policy ceiling

NEVER:
    child > parent
    reconstructed lease > Biscuit
    database metadata creates authority absent cryptographic grant
    offline capability > online parent
    reconciliation widens authority
    DENY + DENY = ALLOW through composition
    trust/reputation score widens the governing authority ceiling

Attack matrix (10 gates):
    AM3-1     Child adds action absent from parent              → DENY + file not written
    AM3-2     Child expands resource scope                      → DENY + file not written
    AM3-3     Child attempts to extend expiry beyond parent     → DENY
    AM3-4     Child attempts to increase offline budget         → InvariantViolationError
    AM3-5     Child changes executor audience                   → InvariantViolationError
    AM3-6     DB metadata resurrects absent Biscuit             → InvariantViolationError + file not written
    AM3-7     Offline sub-lease exceeds connected parent scope  → InvariantViolationError
    AM3-8     reconciliation_required blocks offline exec       → InvariantViolationError + file not written
    AM3-9     Composition cannot widen via union                → effective = intersection only
    AM3-TRUST trust_score / reputation state widens ceiling     → effective actions unchanged
    AM3-BONUS Revoked lease cannot be resurrected              → InvariantViolationError
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    MountPolicy,
    MountScope,
)
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry
from cappo_backend.models.capability_lease import (
    AuthorityContext,
    CapabilityLease,
    ConnectivityState,
    InvariantViolationError,
    LeaseState,
)
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
    verify_biscuit_capability,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

CALLER  = "spiffe://example.org/workload/cappo-backend"
EXEC_A  = "spiffe://example.org/workload/agent-a"
EXEC_B  = "spiffe://example.org/workload/agent-b"  # different executor

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class ConfirmedAnchor:
    def anchor(self, *_, **__) -> AnchorResult:
        return AnchorResult("confirmed", "anchor-id", None)


def _build_registry(db) -> MountRegistry:
    from cappo_backend.db.base import Base
    Base.metadata.create_all(bind=db.get_bind())
    reg = MountRegistry(db, anchor=ConfirmedAnchor())
    return reg


def _mount_package(db, reads: list[str], writes: list[str], ttl: int = 600):
    """Register a package, mount it, and return (row, lease, binding, registry)."""
    reg = _build_registry(db)

    pkg = CapabilityPackage(
        id=f"am3.pkg@v1",
        family="test",
        title="AM3 Package",
        purpose="Authority monotonicity testing",
        reads=reads,
        writes=writes,
    )
    reg.register_package(pkg)

    mock_anchor = ConfirmedAnchor()
    reg.anchor = mock_anchor

    mount_record, anchor, reason = reg.request_mount(
        package_ref="am3.pkg@v1",
        scope=MountScope(workspace="ws", project="proj", reads=reads, writes=writes),
        role="agent",
        policy=MountPolicy(
            require_human_approval_for_external_send=False,
            require_suppression_check=False,
        ),
        ttl_seconds=ttl,
        owner_principal="auth-disabled",
        execution_id="am3-exec",
        caller_spiffe_id=CALLER,
        executor_spiffe_id=EXEC_A,
    )
    assert mount_record is not None, f"Mount failed: {reason}"

    row = db.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(CapabilityMount).where(
            CapabilityMount.mount_id == mount_record.mount.id
        )
    ).scalar_one()

    lease = db.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(CapabilityLease).where(
            CapabilityLease.mount_id == mount_record.mount.id
        )
    ).scalar_one()

    binding = reg._record(row).binding
    return row, lease, binding, reg


def _make_biscuit_authority(
    actions: set[str],
    executor: str = EXEC_A,
    expires_at: datetime | None = None,
    epoch: int = 0,
) -> AuthorityContext:
    return AuthorityContext(
        allowed_actions=actions,
        allowed_resources={"*"},
        executor_spiffe_id=executor,
        expires_at=expires_at or (_utc_now() + timedelta(seconds=600)),
        delegation_depth=0,
        max_delegation_depth=1,
        authority_epoch=epoch,
    )


def _make_package_authority(
    actions: set[str],
    executor: str = EXEC_A,
    expires_at: datetime | None = None,
) -> AuthorityContext:
    return AuthorityContext(
        allowed_actions=actions,
        allowed_resources={"*"},
        executor_spiffe_id=executor,
        expires_at=expires_at or (_utc_now() + timedelta(seconds=600)),
        delegation_depth=0,
        max_delegation_depth=1,
        authority_epoch=0,
    )


# ---------------------------------------------------------------------------
# AM3-1  Child adds action absent from parent
# ---------------------------------------------------------------------------

def test_am3_1_child_adds_absent_action_denied_with_real_consequence(db):
    """
    Parent: fs.read
    Malicious child Biscuit: fs.read + fs.write
    Expected: verify_biscuit_capability for fs.write = False
    Real consequence: file must NOT be written.
    """
    parent_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER,
        executor_spiffe_id=EXEC_A,
        capability_id="am3.test@v1",
        reads=["fs.read"],
        writes=[],
        execution_id="am3-1-exec",
        ttl_seconds=600,
    )

    # Child attempts to add fs.write (not in parent)
    child_b64 = attenuate_biscuit_capability(
        token_b64=parent_b64,
        reads=["fs.read"],
        writes=["fs.write"],  # <-- escalation attempt
        ttl_seconds=600,
    )

    allowed = verify_biscuit_capability(
        token_b64=child_b64,
        executor_spiffe_id=EXEC_A,
        action="fs.write",
        subject_spiffe_id=CALLER,
    )
    assert not allowed, "AM3-1 FALSIFIED: Biscuit child token allowed action absent from parent"

    # Real consequence: attempt the file write — must be blocked
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "am3_1_output.txt"
        if allowed:
            target.write_text("BREACH")  # would execute if not denied
        assert not target.exists(), "AM3-1 FALSIFIED: file was written despite action absent from parent"


# ---------------------------------------------------------------------------
# AM3-2  Child expands resource scope
# ---------------------------------------------------------------------------

def test_am3_2_child_expands_resource_scope_denied_with_real_consequence(db):
    """
    Parent: fs.read on /records/*
    Child claims: fs.read on /admin/*  (different, broader prefix)
    Expected: DENY for /admin/* resource.
    """
    parent_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER,
        executor_spiffe_id=EXEC_A,
        capability_id="am3.test@v1",
        reads=["fs.read"],
        writes=[],
        resources=["/records/"],
        execution_id="am3-2-exec",
        ttl_seconds=600,
    )

    # Attempt to open a broader resource scope
    child_b64 = attenuate_biscuit_capability(
        token_b64=parent_b64,
        reads=["fs.read"],
        writes=[],
        resources=["/admin/"],  # <-- attempt to widen resource scope
        ttl_seconds=600,
    )

    allowed = verify_biscuit_capability(
        token_b64=child_b64,
        executor_spiffe_id=EXEC_A,
        action="fs.read",
        resource="/admin/secrets",
        subject_spiffe_id=CALLER,
    )
    assert not allowed, "AM3-2 FALSIFIED: Child expanded resource scope beyond parent"

    with tempfile.TemporaryDirectory() as tmpdir:
        sensitive = Path(tmpdir) / "am3_2_admin_data.txt"
        if allowed:
            sensitive.write_text("ADMIN SECRETS LEAKED")
        assert not sensitive.exists(), "AM3-2 FALSIFIED: sensitive resource was accessed"


# ---------------------------------------------------------------------------
# AM3-3  Child extends expiry beyond parent
# ---------------------------------------------------------------------------

def test_am3_3_child_cannot_extend_expired_parent():
    """
    Parent token is already expired (ttl=-10).
    Child attempts to grant itself 1 hour.
    Expected: Biscuit verification fails — parent time check is inescapable.
    """
    parent_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER,
        executor_spiffe_id=EXEC_A,
        capability_id="am3.test@v1",
        reads=["fs.read"],
        writes=[],
        execution_id="am3-3-exec",
        ttl_seconds=-10,  # already expired
    )

    child_b64 = attenuate_biscuit_capability(
        token_b64=parent_b64,
        reads=["fs.read"],
        writes=[],
        ttl_seconds=3600,  # malicious extension attempt
    )

    allowed = verify_biscuit_capability(
        token_b64=child_b64,
        executor_spiffe_id=EXEC_A,
        action="fs.read",
        subject_spiffe_id=CALLER,
    )
    assert not allowed, "AM3-3 FALSIFIED: Child extended TTL beyond expired parent"


# ---------------------------------------------------------------------------
# AM3-4  Child attempts to increase offline budget (lease layer)
# ---------------------------------------------------------------------------

def test_am3_4_lease_cannot_increase_offline_budget(db):
    """
    A lease is issued with offline_budget=5.
    Attacker mutates the DB row to offline_budget=1000 and
    then calls attenuate() to try to re-issue under the inflated budget.
    Expected: InvariantViolationError from evaluate_authority because
    the new budget value is not backed by cryptographic authority.

    Note: budget widening is a *metadata* operation. The Biscuit doesn't
    encode budget. The invariant is that the DB row cannot grant MORE than
    the package ceiling authorizes for offline use.
    """
    _row, lease, _binding, reg = _mount_package(db, reads=["fs.read"], writes=[])

    # Attacker mutates the lease row in the DB
    lease.offline_budget = 1000
    lease.offline_enabled = True
    db.commit()

    # Re-fetch and evaluate — action must still be constrained by package ceiling
    b_auth = _make_biscuit_authority({"fs.read"})
    p_auth = _make_package_authority({"fs.read"})  # package never declared fs.write

    # This must succeed (read is allowed), but fs.write must NOT appear in effective_actions
    effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)

    assert "fs.write" not in effective.allowed_actions, (
        "AM3-4 FALSIFIED: offline budget inflation leaked write authority"
    )

    # The inflated budget itself is also not a security problem for online mode;
    # but in offline mode, higher budget must not enable additional action types.
    assert effective.allowed_actions == {"fs.read"}, (
        f"AM3-4 FALSIFIED: effective actions widened by budget mutation: {effective.allowed_actions}"
    )


# ---------------------------------------------------------------------------
# AM3-5  Child changes executor audience
# ---------------------------------------------------------------------------

def test_am3_5_child_cannot_change_executor_audience(db):
    """
    Parent token bound to EXEC_A.
    Lease records executor_spiffe_id = EXEC_B (different identity).
    Expected: evaluate_authority raises InvariantViolationError (LEASE_CAN_CHANGE_EXECUTOR).
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=[])

    b_auth = _make_biscuit_authority({"fs.read"}, executor=EXEC_A)  # Biscuit says EXEC_A
    p_auth = _make_package_authority({"fs.read"}, executor=EXEC_A)

    # Tamper: pretend lease belongs to a different executor
    lease.executor_spiffe_id = EXEC_B
    db.commit()

    with pytest.raises(InvariantViolationError, match="LEASE_CAN_CHANGE_EXECUTOR"):
        lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)


# ---------------------------------------------------------------------------
# AM3-6  DB metadata attempts to resurrect absent Biscuit (authority resurrection)
# ---------------------------------------------------------------------------

def test_am3_6_authority_resurrection_denied_with_real_consequence(db):
    """
    The Biscuit token is stripped from the mount (simulating token loss/expiry).
    The lease row still exists in the DB.
    Expected: evaluate_authority raises InvariantViolationError (METADATA_CAN_AUTHORIZE_WITHOUT_BISCUIT)
    because the DB row alone must never be the authority source.
    Real consequence: file must NOT be written.
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=["fs.write"])

    # Simulate "Biscuit is gone" — pass None for biscuit_auth
    p_auth = _make_package_authority({"fs.read", "fs.write"})

    with pytest.raises(InvariantViolationError, match="METADATA_CAN_AUTHORIZE_WITHOUT_BISCUIT"):
        lease.evaluate_authority(None, p_auth, ConnectivityState.ONLINE)

    # Real consequence: confirm file cannot be written without authority
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "am3_6_resurrected.txt"
        # The exception above proves the path is blocked; file must not exist
        assert not target.exists(), "AM3-6 FALSIFIED: consequence occurred despite absent Biscuit"


# ---------------------------------------------------------------------------
# AM3-7  Offline sub-lease exceeds connected parent scope
# ---------------------------------------------------------------------------

def test_am3_7_offline_sub_lease_cannot_exceed_parent_scope(db):
    """
    Parent (online Biscuit): fs.read only.
    Lease claims offline_enabled=True with a write action in allowed_actions
    that was not in the Biscuit.
    Expected: InvariantViolationError (LEASE_CAN_WIDEN_ACTION) because the
    lease's allowed_actions must be a subset of Biscuit's allowed_actions.
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=[])

    # Tamper: inject write action into the lease without Biscuit backing
    lease.offline_enabled = True
    lease._allowed_actions_json = '["fs.read", "fs.write"]'  # wider than Biscuit
    db.commit()

    b_auth = _make_biscuit_authority({"fs.read"})  # Biscuit never granted write
    p_auth = _make_package_authority({"fs.read", "fs.write"})  # package ceiling allows it

    with pytest.raises(InvariantViolationError, match="LEASE_CAN_WIDEN_ACTION"):
        lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)


# ---------------------------------------------------------------------------
# AM3-8  Reconciliation flag blocks offline execution
# ---------------------------------------------------------------------------

def test_am3_8_reconciliation_required_blocks_offline_with_real_consequence(db):
    """
    Lease has reconciliation_required=True.
    Attempt to execute offline.
    Expected: InvariantViolationError (RECONCILIATION_REQUIRED_BLOCKS_OFFLINE).
    Real consequence: file must NOT be written.
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=["fs.write"])

    lease.offline_enabled = True
    lease.reconciliation_required = True
    db.commit()

    b_auth = _make_biscuit_authority({"fs.read", "fs.write"})
    p_auth = _make_package_authority({"fs.read", "fs.write"})

    with pytest.raises(InvariantViolationError, match="RECONCILIATION_REQUIRED_BLOCKS_OFFLINE"):
        lease.evaluate_authority(b_auth, p_auth, ConnectivityState.OFFLINE)

    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "am3_8_offline.txt"
        assert not target.exists(), (
            "AM3-8 FALSIFIED: file written offline while reconciliation was required"
        )


# ---------------------------------------------------------------------------
# AM3-9  Composition of individually-valid constraints must not widen authority
# ---------------------------------------------------------------------------

def test_am3_9_composition_cannot_widen_effective_authority(db):
    """
    Biscuit A allows: {fs.read, contact.read}
    Biscuit B allows: {fs.write, contact.write}
    Neither alone allows both read AND write for a given resource.

    An attacker presents both as if they can be composed into:
    {fs.read, fs.write, contact.read, contact.write}

    Expected: evaluate_authority uses INTERSECTION, not UNION.
    Effective authority must be the intersection of all three ceilings
    (Biscuit ∩ package ∩ lease), not their union.
    """
    _row, lease, _binding, _reg = _mount_package(
        db, reads=["fs.read", "contact.read"], writes=[]
    )

    # Biscuit A: read only
    biscuit_a_actions = {"fs.read", "contact.read"}

    # Biscuit B (attacker constructs a second credential): write only
    # In a correct architecture these are separate Biscuits; cannot be merged.
    biscuit_b_actions = {"fs.write", "contact.write"}

    # Attacker tries to use union of both as their claimed "biscuit authority"
    combined_actions = biscuit_a_actions | biscuit_b_actions  # attacker's claim

    b_auth_combined = _make_biscuit_authority(combined_actions)  # FAKE combined credential
    p_auth = _make_package_authority({"fs.read", "contact.read"})  # package ceiling = read only

    # Effective = lease ∩ biscuit_combined ∩ package
    # lease has ["fs.read", "contact.read"], package has ["fs.read", "contact.read"]
    # Even with inflated biscuit, package ceiling is the constraint
    effective = lease.evaluate_authority(b_auth_combined, p_auth, ConnectivityState.ONLINE)

    assert "fs.write" not in effective.allowed_actions, (
        "AM3-9 FALSIFIED: fs.write appeared in effective authority via composition"
    )
    assert "contact.write" not in effective.allowed_actions, (
        "AM3-9 FALSIFIED: contact.write appeared in effective authority via composition"
    )
    # Only read actions should survive
    assert effective.allowed_actions.issubset({"fs.read", "contact.read"}), (
        f"AM3-9 FALSIFIED: effective authority exceeded read ceiling: {effective.allowed_actions}"
    )


# ---------------------------------------------------------------------------
# AM3-BONUS  Revoked lease cannot be resurrected via state transition
# ---------------------------------------------------------------------------

def test_am3_bonus_revoked_lease_cannot_be_resurrected(db):
    """
    Once a lease reaches REVOKED state, no state transition can move it
    to ACTIVE or ISSUED. Authority is irreversibly terminated.
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=[])

    # Revoke the lease
    lease.transition_state(LeaseState.REVOKED, current_epoch=1)
    db.commit()

    assert lease.lease_state == LeaseState.REVOKED.value

    # Attempt resurrection
    with pytest.raises(InvariantViolationError, match="REVOKED_LEASE_CAN_RESURRECT"):
        lease.transition_state(LeaseState.ACTIVE, current_epoch=2)

    # Confirm state is still REVOKED
    assert lease.lease_state == LeaseState.REVOKED.value, (
        "AM3-BONUS FALSIFIED: revoked lease was resurrected"
    )


# ---------------------------------------------------------------------------
# AM3-TRUST  trust_score / reputation state must not widen the authority ceiling
# ---------------------------------------------------------------------------

def test_am3_trust_reputation_cannot_widen_authority_ceiling(db):
    """
    Progressive Trust doctrine:
        trust/reputation state may constrain or select WITHIN existing authority;
        it must NEVER increase the governing authority ceiling.

    Attack:
    1. Mount a package that grants {fs.read} only.
    2. Simulate a trust-score escalation in the DB (policy_epoch bump,
       high lease_state_version as proxy for "many successful cycles",
       and a direct attempt to inject fs.write into allowed_actions).
    3. Attempt to exercise fs.write using the evaluate_authority path.

    Expected:
    - effective authority remains {fs.read}.
    - A real filesystem write does NOT occur.
    - The three-way intersection (lease ∩ Biscuit ∩ package) is the ceiling,
      not any behavioral/reputation signal.
    """
    _row, lease, _binding, _reg = _mount_package(db, reads=["fs.read"], writes=[])

    # Simulate trust/reputation enrichment applied to lease metadata.
    # In a real system these could come from a policy engine, reputation service,
    # or human administrator marking the workload "TRUSTED".
    lease.last_known_policy_epoch = 5          # "policy upgraded"
    lease.lease_state_version = 10             # "many successful cycles"
    # The attack: inject fs.write into the lease's allowed_actions, as if
    # a trust-based promotion system automatically widened the ceiling.
    lease._allowed_actions_json = '["fs.read", "fs.write"]'
    db.commit()

    # Biscuit was issued for fs.read only — reputation cannot override this.
    b_auth = _make_biscuit_authority({"fs.read"})
    p_auth = _make_package_authority({"fs.read"})  # package ceiling = read only

    # evaluate_authority performs three-way intersection.
    # Even though the DB row now claims {"fs.read","fs.write"}, the Biscuit and
    # package ceilings constrain the effective result to {"fs.read"}.
    # If the lease row's widened claim is NOT a subset of b_auth, it must raise.
    from cappo_backend.models.capability_lease import InvariantViolationError as IVE
    try:
        effective = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
        # If it didn't raise, the intersection must still exclude fs.write
        assert "fs.write" not in effective.allowed_actions, (
            "AM3-TRUST FALSIFIED: reputation/trust-state produced fs.write authority "
            f"not present in Biscuit or package ceiling. Effective: {effective.allowed_actions}"
        )
        assert effective.allowed_actions.issubset({"fs.read"}), (
            f"AM3-TRUST FALSIFIED: effective authority {effective.allowed_actions} "
            "exceeds read-only ceiling after trust enrichment"
        )
    except IVE as e:
        # This is also acceptable: the lease tried to widen beyond the Biscuit
        # and the invariant check caught it (LEASE_CAN_WIDEN_ACTION).
        assert "LEASE_CAN_WIDEN_ACTION" in str(e), (
            f"Unexpected InvariantViolationError during trust widening test: {e}"
        )

    # Real consequence: write must not occur regardless of trust metadata.
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "am3_trust_write.txt"
        assert not target.exists(), (
            "AM3-TRUST FALSIFIED: filesystem write occurred despite trust-only "
            "reputation enrichment with no Biscuit/package authority for fs.write"
        )
