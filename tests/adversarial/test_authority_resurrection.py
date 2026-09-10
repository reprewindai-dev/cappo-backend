"""
Adversarial Test: Authority Resurrection / Rollback-Resistance Packet
=====================================================================

Predator Engineering + Veklom Doctrine applied.

OBJECTIVE (falsifiable):
  Historical authority MUST remain non-executable (target-effect count = 0)
  Historical evidence MAY remain valid (readable).

ATTACK VECTORS:
  1. DB rollback (nonce_consumed = False) — same key
  2. DB rollback + key rotation K1 -> K2
  3. DB rollback + policy rollback (action removed from package)
  4. Triple rollback (all three simultaneously)
  5. Evidence validity positive check (evidence != authority)

QUALIFICATION CRITERION:
  - tests 1-4: Decision.DENY, target-effect count = 0
  - test 5: evidence readable, authority NOT re-executable
"""

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
)
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.config import Settings
from cappo_backend.services.mount_pgl import AuditPGLAnchor

CALLER_SPIFFE = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/my-agent"
CAPABILITY_ID = "bank-transfer@v1"
PROTECTED_ACTION = "transfer_funds"


def _gen_key_hex() -> str:
    from biscuit_auth import KeyPair
    return KeyPair().private_key.to_bytes().hex()


def _clear_key_state() -> None:
    import cappo_backend.security.biscuit as mod
    mod._ROOT_KEY_PAIR = None


def _make_settings(hex_key: str):
    return Settings(biscuit_root_private_key_hex=hex_key, environment="test")


def _build_registry(db: Session, package_reads: list) -> MountRegistry:
    # Point PGL anchor at an unreachable IP so it returns pending_reconciliation (accepted)
    anchor_settings = Settings(
        pgl_ledger_url="http://1.1.1.1:80",
        pgl_ledger_timeout_ms=100,
    )
    reg = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=anchor_settings))
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.bank",
            title="Bank Transfer",
            purpose="Adversarial resurrection test capability",
            reads=package_reads,
            writes=[],
        )
    )
    return reg


def _mount_and_execute(db: Session) -> tuple:
    """Mount and execute one action. Returns (mount_record, registry)."""
    exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    reg = _build_registry(db, package_reads=[PROTECTED_ACTION])
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[PROTECTED_ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=600,
        owner_principal="auth-disabled",
        execution_id=exec_id,
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    d1, r1, _, _ = reg.evaluate(
        mount_id=mount_record.mount.id,
        action=PROTECTED_ACTION,
        token_id=mount_record.token.token_id,
        nonce=mount_record.token.nonce,
        owner_principal="auth-disabled",
        spiffe_fields={
            "caller_spiffe_id": CALLER_SPIFFE,
            "executor_spiffe_id": EXECUTOR_SPIFFE,
            "caller_cert_sha256": "abcd",
            "trust_domain": "example.org",
        },
    )
    assert d1 == Decision.ALLOW, f"Setup first-execute failed: {r1}"
    db.commit()
    return mount_record, reg


def _rollback_nonce(db: Session, mount_id: str) -> None:
    """Simulate DB snapshot restore by reverting nonce_consumed = False."""
    from sqlalchemy import select

    from cappo_backend.models.capability_mount import CapabilityMount
    row = db.execute(
        select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
    ).scalar_one_or_none()
    assert row is not None, "Mount row not found for rollback"
    row.nonce_consumed = False
    db.commit()


def test_resurrection_db_rollback_same_key(db: Session):
    """
    Attack: Roll nonce_consumed=False in DB, replay same token under same key.
    Invariant: Must DENY. The nonce guard lives in the DB row.
    Finding: If this passes, the nonce is the SOLE protection and is rollback-susceptible.
    An external monotonic ledger is required for full rollback-resistance.
    """
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, reg = _mount_and_execute(db)
        # Simulate DB restore
        _rollback_nonce(db, mount_record.mount.id)
        # Replay with rolled-back nonce
        d_attack, r_attack, _, _ = reg.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )
        assert d_attack == Decision.DENY, (
            f"INVARIANT BROKEN: DB rollback alone resurrected authority. "
            f"Decision={d_attack}, reason={r_attack}. "
            f"The nonce_consumed flag is rollback-susceptible without an external monotonic ledger."
        )
        assert r_attack == "token_replay_receipt", (
            f"Expected token_replay_receipt (receipt-based guard) but got: {r_attack}. "
            f"The rollback-resistance receipt check is not firing."
        )


def test_resurrection_db_rollback_key_rotation(db: Session):
    """
    Attack: DB rollback + key rotation K1->K2.
    Old Biscuit minted under K1 cannot be verified under K2.
    Expected denial: missing_cryptographic_authority.
    """
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)

    _rollback_nonce(db, mount_record.mount.id)

    hex_k2 = _gen_key_hex()
    settings_k2 = _make_settings(hex_k2)
    assert hex_k1 != hex_k2

    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k2):
        reg_k2 = _build_registry(db, package_reads=[PROTECTED_ACTION])
        d_attack, r_attack, _, _ = reg_k2.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )

    assert d_attack == Decision.DENY, (
        f"INVARIANT BROKEN: Key rotation + DB rollback resurrected authority. "
        f"Decision={d_attack}, reason={r_attack}"
    )
    # The receipt guard fires before the cryptographic check when the receipt row survives.
    # Both reasons prove the historical authority is non-executable.
    assert r_attack in ("missing_cryptographic_authority", "token_replay_receipt"), (
        f"Expected missing_cryptographic_authority or token_replay_receipt but got: {r_attack}"
    )


def test_resurrection_db_rollback_policy_rollback(db: Session):
    """
    Attack: DB rollback + policy rollback (action removed from package).
    Even if the Biscuit still has the action in its facts, the package ceiling
    no longer allows it. The intersection must be empty -> DENY.
    """
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)

    _rollback_nonce(db, mount_record.mount.id)

    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        reg_no_policy = _build_registry(db, package_reads=[])  # action removed
        d_attack, r_attack, _, _ = reg_no_policy.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )

    assert d_attack == Decision.DENY, (
        f"INVARIANT BROKEN: Policy rollback + DB rollback resurrected authority. "
        f"Decision={d_attack}, reason={r_attack}"
    )
    assert r_attack in (
        "lease_invariant_violation",
        "not_in_capability_profile",
        "blocked_action",
        "token_replay_receipt",  # receipt guard fires before policy check when receipt survives rollback
    ), f"Unexpected denial reason: {r_attack}"


def test_resurrection_triple_rollback(db: Session):
    """
    Maximum adversarial: DB rollback + key rotation + policy rollback simultaneously.
    All three layers must fail. Target-effect count = 0.
    """
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)

    _rollback_nonce(db, mount_record.mount.id)

    hex_k2 = _gen_key_hex()
    settings_k2 = _make_settings(hex_k2)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k2):
        reg_degraded = _build_registry(db, package_reads=[])  # policy also rolled back
        d_attack, r_attack, _, _ = reg_degraded.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )

    assert d_attack == Decision.DENY, (
        f"CRITICAL INVARIANT BROKEN: Triple rollback resurrected authority. "
        f"Decision={d_attack}, reason={r_attack}."
    )


def test_historical_evidence_remains_valid(db: Session):
    """
    Positive invariant: historical EVIDENCE must remain readable (evidence != authority).
    The Biscuit token (the historical artifact) must remain parseable and return the
    correct authority context — but this readability does NOT grant executability.
    """
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)
        token_b64 = mount_record.token.biscuit_token
        assert token_b64 is not None, "No Biscuit token minted — evidence cannot be validated"

        from cappo_backend.security.biscuit import extract_authority_context
        ctx = extract_authority_context(token_b64)
        assert ctx is not None, "Historical evidence is not readable immediately after mint"
        assert PROTECTED_ACTION in ctx.allowed_actions, (
            f"Historical evidence missing expected action. Got: {ctx.allowed_actions}"
        )

    # After simulated process restart, evidence must still be readable under same key
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        from cappo_backend.security.biscuit import extract_authority_context
        ctx_after = extract_authority_context(token_b64)
        assert ctx_after is not None, "Historical evidence became unreadable after simulated restart"
        assert PROTECTED_ACTION in ctx_after.allowed_actions

def _delete_receipts(db) -> None:
    from sqlalchemy import text
    db.execute(text("DELETE FROM capability_action_receipts"))
    db.commit()

def test_t_iso_1_absent_receipt_guard_key_rotation(db):
    # T-ISO-1: Absent receipt guard -> key-rotation rollback must still deny (missing_cryptographic_authority)
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    from unittest.mock import patch
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)

    _rollback_nonce(db, mount_record.mount.id)
    _delete_receipts(db)

    hex_k2 = _gen_key_hex()
    settings_k2 = _make_settings(hex_k2)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k2):
        reg_k2 = _build_registry(db, package_reads=[PROTECTED_ACTION])
        d_attack, r_attack, _, _ = reg_k2.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )

    assert d_attack == Decision.DENY
    assert r_attack == "missing_cryptographic_authority"


def test_t_iso_2_absent_receipt_guard_policy_rollback(db):
    # T-ISO-2: Absent receipt guard -> policy rollback must still deny
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    from unittest.mock import patch
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        mount_record, _ = _mount_and_execute(db)

    _rollback_nonce(db, mount_record.mount.id)
    _delete_receipts(db)

    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        reg_no_policy = _build_registry(db, package_reads=[])  # action removed
        d_attack, r_attack, _, _ = reg_no_policy.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )

    assert d_attack == Decision.DENY
    assert r_attack in ("lease_invariant_violation", "not_in_capability_profile", "blocked_action")


def test_t_iso_3_nonce_valid_key_rotated(db):
    # T-ISO-3: Nonce/receipt valid, key rotated (stale generation) -> key guard denies
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    from unittest.mock import patch
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        exec_id = f"exec_{uuid.uuid4().hex[:8]}"
        reg = _build_registry(db, package_reads=[PROTECTED_ACTION])
        mount_record, _, _ = reg.request_mount(
            package_ref=CAPABILITY_ID,
            scope=MountScope(workspace="ws_1", project="prj_1", reads=[PROTECTED_ACTION], writes=[]),
            role="agent",
            policy=MountPolicy(),
            ttl_seconds=600,
            owner_principal="auth-disabled",
            execution_id=exec_id,
            caller_spiffe_id=CALLER_SPIFFE,
            executor_spiffe_id=EXECUTOR_SPIFFE,
        )
    
    hex_k2 = _gen_key_hex()
    settings_k2 = _make_settings(hex_k2)
    _clear_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k2):
        reg_k2 = _build_registry(db, package_reads=[PROTECTED_ACTION])
        d_attack, r_attack, _, _ = reg_k2.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )
    
    assert d_attack == Decision.DENY
    assert r_attack == "missing_cryptographic_authority"


def test_t_iso_4_nonce_key_valid_policy_stale(db):
    # T-ISO-4: Nonce/receipt/key valid, policy stale -> policy guard denies
    hex_k1 = _gen_key_hex()
    settings_k1 = _make_settings(hex_k1)
    _clear_key_state()
    from unittest.mock import patch
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        exec_id = f"exec_{uuid.uuid4().hex[:8]}"
        reg = _build_registry(db, package_reads=[PROTECTED_ACTION])
        mount_record, _, _ = reg.request_mount(
            package_ref=CAPABILITY_ID,
            scope=MountScope(workspace="ws_1", project="prj_1", reads=[PROTECTED_ACTION], writes=[]),
            role="agent",
            policy=MountPolicy(),
            ttl_seconds=600,
            owner_principal="auth-disabled",
            execution_id=exec_id,
            caller_spiffe_id=CALLER_SPIFFE,
            executor_spiffe_id=EXECUTOR_SPIFFE,
        )
        
        reg_stale_policy = _build_registry(db, package_reads=[])
        d_attack, r_attack, _, _ = reg_stale_policy.evaluate(
            mount_id=mount_record.mount.id,
            action=PROTECTED_ACTION,
            token_id=mount_record.token.token_id,
            nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd",
                "trust_domain": "example.org",
            },
        )
        
    assert d_attack == Decision.DENY
    assert r_attack in ("lease_invariant_violation", "not_in_capability_profile", "blocked_action")
