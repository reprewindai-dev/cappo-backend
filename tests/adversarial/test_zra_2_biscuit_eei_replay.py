"""
ZRA-1 Layer 2: Biscuit and EEI Replay Falsifier

Hypothesis: Zero Residual Agency requires that all execution-specific authority 
and tokens are rendered inert post-termination, across all enforcement paths.

Falsifiers:
1. Replay the same EEI (token_id + nonce) through CAPPO `evaluate()` post-termination.
2. Replay the same Biscuit through CAPPO `evaluate()` post-termination (implicit via EEI).
3. Evaluate the Biscuit token offline via `verify_biscuit_capability()`.
4. Try a cached EEI+Biscuit combination (offline vs online).

The falsifier proves exactly which mechanisms successfully block replay 
(e.g., central database mount state) and which mechanisms fail (e.g., offline 
Biscuit evaluation lacking revocation/freshness checks).
"""

import pytest
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import Decision, MountPolicy, MountScope, UnmountReason
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.security.biscuit import verify_biscuit_capability
from cappo_backend.services.mount_pgl import AuditPGLAnchor

CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/zra1-agent"
CAPABILITY_ID  = "test.resource@v1"
ACTION         = "resource.read"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_zra_2_biscuit_and_eei_replay(db: Session):
    reg = _build_registry(db)
    
    # 0. Register capability
    from cappo_backend.capability_mount.models import CapabilityPackage
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="ZRA-1 biscuit replay test",
            reads=[ACTION],
            writes=[],
        )
    )

    # 1. Setup valid mount
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="zra1-exec-003",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce
    biscuit_token = mount_record.token.biscuit_token

    # Ensure Biscuit token was actually minted
    assert biscuit_token is not None, "Biscuit token must be minted for this test."

    # Pre-termination: verify offline biscuit works
    offline_valid_before = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*"
    )
    assert offline_valid_before is True, "Biscuit should be valid before termination."

    # 2. Terminate the execution
    reg.terminate(mount_id=mount_id, reason=UnmountReason.TOKEN_EXPIRY)

    # 3. Behavior 1: Centralized Replay Denied Immediately
    decision, reason, _, _ = reg.evaluate(
        mount_id=mount_id,
        action=ACTION,
        token_id=token_id,
        nonce=nonce,
        owner_principal="auth-disabled",
        spiffe_fields={
            "caller_spiffe_id": CALLER_SPIFFE,
            "executor_spiffe_id": EXECUTOR_SPIFFE,
            "caller_cert_sha256": "abcd" * 16,
            "trust_domain": "example.org",
        },
    )
    assert decision == Decision.DENY
    assert reason == "terminated", f"Expected denial due to 'terminated', got '{reason}'"

    # 4. Behavior 2: Offline replay denied ONCE the verifier possesses the new epoch state
    from cappo_backend.models.capability_lease import CapabilityLease
    lease_after = db.query(CapabilityLease).filter_by(lease_id=f"lease_{mount_id}").first()
    assert lease_after is not None
    # Simulate a synced offline node that received the latest revocation_epoch for this scope
    from cappo_backend.security.biscuit import TrustedRevocationState
    trusted_state = TrustedRevocationState()
    trusted_state.sync_epochs({lease_after.revocation_scope: lease_after.revocation_epoch})
    
    synced_offline_valid_after = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*",
        trusted_state=trusted_state
    )
    assert synced_offline_valid_after is False, "Synced offline verifier should deny the token due to epoch advancement."

    # 5. Behavior 3: Truly disconnected offline replay remains bounded ONLY by token expiry
    # Simulate a disconnected verifier that has NOT received the epoch update
    disconnected_offline_valid_after = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*"
    )
    assert disconnected_offline_valid_after is True, "Disconnected offline verifier MUST accept the token until natural expiry, proving bounded ZRA."

def test_zra_2_transaction_rollback_partial_transition(db: Session, monkeypatch):
    from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
    from cappo_backend.models.capability_mount import CapabilityMount
    reg = _build_registry(db)
    from cappo_backend.capability_mount.models import CapabilityPackage
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="ZRA-1 biscuit replay test",
            reads=[ACTION],
            writes=[],
        )
    )
    
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
    # Mint a biscuit that expires instantly to simulate crossing the boundary
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
    from cappo_backend.security.biscuit import TrustedRevocationState, mint_biscuit_capability
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
    
    # 1. Verifier has a local trusted epoch of 5
    trusted_state = TrustedRevocationState()
    trusted_state.sync_epochs({scope: 5})
    
    # 2. A stale sync message attempts to regress the epoch to 0
    trusted_state.sync_epochs({scope: 0})
    
    # 3. The TrustedRevocationState monotonic property guarantees it remains 5
    assert trusted_state.known_epochs[scope] == 5, "TrustedRevocationState must enforce monotonic progression"
    
    valid = verify_biscuit_capability(
        token_b64=biscuit_token,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION,
        resource="*",
        trusted_state=trusted_state
    )
    
    assert valid is False, "Verifier must reject token if known epoch remains higher due to monotonic enforcement"
