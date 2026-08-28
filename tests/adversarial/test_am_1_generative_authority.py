"""
AM-1 — Authority Monotonicity

Hypothesis: The architecture is designed so that effective authority at time t is the 
intersection of independently constraining terms; lifecycle metadata may preserve or 
reduce authority but must never create it absent a new upstream cryptographic grant. 
AM-1 exists to prove that no implementation transition widens effective authority without 
such a grant.

Test: Attempt to use lifecycle changes, attenuations, or offline/online transitions 
to gain permissions not in the original token.
"""

from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy, MountScope
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
    verify_biscuit_capability,
)

CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/am1-agent"
CAPABILITY_ID  = "test.resource@v1"
ACTION_READ    = "resource.read"
ACTION_WRITE   = "resource.write"
EXEC_ID        = "am1-exec-001"
TTL            = 300

def test_am_1_attenuation_widening():
    """
    Test that a child token cannot be attenuated to grant rights broader than 
    the parent token.
    """
    parent_token_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION_READ],
        writes=[],
        execution_id=EXEC_ID,
        ttl_seconds=TTL,
    )

    child_token_b64 = attenuate_biscuit_capability(
        token_b64=parent_token_b64,
        reads=[ACTION_READ],
        writes=[ACTION_WRITE], # Malicious escalation
        ttl_seconds=TTL,
    )

    is_valid = verify_biscuit_capability(
        token_b64=child_token_b64,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION_WRITE,
        subject_spiffe_id=CALLER_SPIFFE,
    )
    
    assert not is_valid, "AM-1 Falsified: Biscuit token allowed privilege escalation during attenuation."


def test_am_1_ttl_extension():
    """
    Test that a child token cannot extend the TTL beyond the parent token.
    We prove this by minting a parent token that is already expired (negative TTL).
    The child token attempts to extend the TTL to 1 hour in the future.
    Verification MUST fail because the parent's time constraint is inescapable.
    """
    parent_token_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION_READ],
        writes=[],
        execution_id=EXEC_ID,
        ttl_seconds=-10,
    )

    child_token_b64 = attenuate_biscuit_capability(
        token_b64=parent_token_b64,
        reads=[ACTION_READ],
        writes=[],
        ttl_seconds=3600, # 1 hour in the future
    )

    is_valid = verify_biscuit_capability(
        token_b64=child_token_b64,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        action=ACTION_READ,
        subject_spiffe_id=CALLER_SPIFFE,
    )
    
    assert not is_valid, "AM-1 Falsified: Biscuit token allowed TTL extension during attenuation."

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.db.base import Base
    Base.metadata.create_all(bind=db.get_bind())
    return MountRegistry(db)

def test_am_1_mount_scope_widening(db: Session):
    reg = _build_registry(db)
    
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
    
    from unittest.mock import MagicMock

    from cappo_backend.capability_mount.service import AnchorResult
    
    mock_anchor = AnchorResult("confirmed", "mock-anchor-id", None)
    reg.anchor.anchor = MagicMock(return_value=mock_anchor)
    
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
        
    # The mount should succeed but silently DROP the malicious WRITE scope
    # because engine.py uses set intersection (package_reads & requested_reads)
    assert mount_record is not None, f"Mount failed: {reason}"
    
    # Verify that the resulting mount grants DO NOT include the malicious write
    assert "test.write" not in mount_record.mount.grants.writes, "AM-1 Falsified: Malicious write scope was granted!"
    assert "test.write" not in mount_record.mount.grants.reads, "AM-1 Falsified: Malicious write scope was granted in reads!"