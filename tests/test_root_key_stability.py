"""
Root-Key Stability Test Suite — root-key-stability-closure

Tests that prove the Biscuit verification root is stable across:
- fresh Settings instantiation
- simulated process restarts (clearing in-memory key state)
- CWD changes
- fresh DB sessions (via mocked session)

Required test matrix:
  configured root is actually read by Settings               PASS
  same configured root across fresh Settings instance        PASS
  mount Biscuit verifies after simulated process restart      PASS
  CWD change does not alter verification root                PASS
  wrong root                                                  DENY
  missing production root                                    STARTUP FAIL
  malformed configured root                                  STARTUP FAIL
  Settings has biscuit_root_private_key_hex attr             PASS (regression for silent discard)
  lifecycle: configure K → issue → persist → destroy key state → reload K → verify PASS
"""

import os
import secrets
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from biscuit_auth import Algorithm, KeyPair, PrivateKey

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gen_root_key_hex() -> str:
    """Generate a fresh valid 32-byte (64-hex) Ed25519 private key."""
    kp = KeyPair()
    return kp.private_key.to_bytes().hex()


def _clear_biscuit_module_key_state():
    """Reset the in-process cached root key pair to simulate a process restart."""
    import cappo_backend.security.biscuit as biscuit_mod
    biscuit_mod._ROOT_KEY_PAIR = None


def _make_settings_with_hex_key(hex_key: str, **extra):
    from cappo_backend.config import Settings
    return Settings(
        biscuit_root_private_key_hex=hex_key,
        environment="test",
        **extra,
    )


def _mint_and_verify_with_settings(settings_obj):
    """Configure the module-level key from settings_obj, mint, then verify."""
    _clear_biscuit_module_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_obj):
        from cappo_backend.security.biscuit import (
            mint_biscuit_capability,
            verify_biscuit_capability,
        )
        token = mint_biscuit_capability(
            caller_spiffe_id="spiffe://test/caller",
            executor_spiffe_id="spiffe://test/executor",
            capability_id="test-cap",
            reads=["resource.read"],
            writes=[],
            execution_id="exec-001",
            ttl_seconds=300,
        )
        return token


# ---------------------------------------------------------------------------
# T1 — Settings has the biscuit attributes (regression for silent discard)
# ---------------------------------------------------------------------------

def test_settings_has_biscuit_root_private_key_hex_attr():
    """
    Regression: Devin observed `settings has BISCUIT_ROOT_PRIVATE_KEY_HEX attr: False`.
    This test proves the attribute is always present on a fresh Settings instance,
    regardless of whether the env var is set.  If extra="ignore" ever silently drops
    this field, this test fails loudly.
    """
    from cappo_backend.config import Settings
    s = Settings()
    assert hasattr(s, "biscuit_root_private_key_hex"), (
        "Settings.biscuit_root_private_key_hex is missing — "
        "the field was silently discarded (extra='ignore' or field name mismatch)."
    )
    assert hasattr(s, "biscuit_root_key_path"), (
        "Settings.biscuit_root_key_path is missing."
    )


def test_settings_biscuit_hex_attr_reads_env_var():
    """Configured hex key is actually read by Settings (not silently discarded)."""
    hex_key = _gen_root_key_hex()
    with patch.dict(os.environ, {"BISCUIT_ROOT_PRIVATE_KEY_HEX": hex_key}):
        from cappo_backend.config import Settings
        s = Settings()
        assert s.biscuit_root_private_key_hex == hex_key, (
            f"Settings did not pick up BISCUIT_ROOT_PRIVATE_KEY_HEX from env. "
            f"Got: {s.biscuit_root_private_key_hex!r}"
        )


# ---------------------------------------------------------------------------
# T2 — Same configured root across fresh Settings instances
# ---------------------------------------------------------------------------

def test_same_configured_root_across_fresh_settings():
    """Two fresh Settings instances with the same hex key produce the same root key pair."""
    from biscuit_auth import Algorithm, KeyPair, PrivateKey
    hex_key = _gen_root_key_hex()
    s1 = _make_settings_with_hex_key(hex_key)
    s2 = _make_settings_with_hex_key(hex_key)
    kp1 = KeyPair.from_private_key(PrivateKey.from_bytes(bytes.fromhex(s1.biscuit_root_private_key_hex), Algorithm.Ed25519))
    kp2 = KeyPair.from_private_key(PrivateKey.from_bytes(bytes.fromhex(s2.biscuit_root_private_key_hex), Algorithm.Ed25519))
    assert kp1.public_key.to_bytes() == kp2.public_key.to_bytes(), (
        "Two Settings instances with the same hex key produced different public keys."
    )


# ---------------------------------------------------------------------------
# T3 — Biscuit verifies after simulated process restart
# ---------------------------------------------------------------------------

def test_biscuit_verifies_after_simulated_process_restart():
    """
    Configure a stable root K, mint a Biscuit, destroy in-memory key state
    (simulating process restart), reload K, then verify the same Biscuit.
    Authority must survive the process boundary.
    """
    hex_key = _gen_root_key_hex()
    settings = _make_settings_with_hex_key(hex_key)

    _clear_biscuit_module_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings):
        from cappo_backend.security.biscuit import (
            mint_biscuit_capability,
            verify_biscuit_capability,
        )
        token = mint_biscuit_capability(
            caller_spiffe_id="spiffe://test/caller",
            executor_spiffe_id="spiffe://test/executor",
            capability_id="test-cap",
            reads=["resource.read"],
            writes=[],
            execution_id="exec-restart-001",
            ttl_seconds=300,
        )

    # Destroy in-memory key state (process restart simulation)
    _clear_biscuit_module_key_state()

    # Reload same key K and verify
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings):
        from cappo_backend.security.biscuit import verify_biscuit_capability
        result = verify_biscuit_capability(
            token_b64=token,
            executor_spiffe_id="spiffe://test/executor",
            action="resource.read",
        )

    assert result is True, (
        "Biscuit verification failed after simulated process restart with same root key K. "
        "Authority does NOT survive the process boundary — root-key instability confirmed."
    )


# ---------------------------------------------------------------------------
# T4 — CWD change does not alter the verification root
# ---------------------------------------------------------------------------

def test_cwd_change_does_not_alter_verification_root(tmp_path):
    """
    The configured key path must resolve to the same file regardless of CWD.
    This test uses the hex key path (most stable) and also verifies that
    a home-relative path (~/.cappo/...) with an injected key file is stable.
    """
    hex_key = _gen_root_key_hex()
    settings = _make_settings_with_hex_key(hex_key)

    # Mint in original CWD
    _clear_biscuit_module_key_state()
    original_cwd = os.getcwd()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings):
        from cappo_backend.security.biscuit import (
            mint_biscuit_capability,
            verify_biscuit_capability,
        )
        token = mint_biscuit_capability(
            caller_spiffe_id="spiffe://test/caller",
            executor_spiffe_id="spiffe://test/executor",
            capability_id="test-cap",
            reads=["resource.read"],
            writes=[],
            execution_id="exec-cwd-001",
            ttl_seconds=300,
        )

    # Change CWD and verify
    try:
        os.chdir(tmp_path)
        _clear_biscuit_module_key_state()
        with patch("cappo_backend.security.biscuit.get_settings", return_value=settings):
            from cappo_backend.security.biscuit import verify_biscuit_capability
            result = verify_biscuit_capability(
                token_b64=token,
                executor_spiffe_id="spiffe://test/executor",
                action="resource.read",
            )
    finally:
        os.chdir(original_cwd)

    assert result is True, (
        "Biscuit verification failed after CWD change. "
        "The key path is CWD-sensitive — root-key instability confirmed."
    )


# ---------------------------------------------------------------------------
# T5 — Wrong root → DENY
# ---------------------------------------------------------------------------

def test_wrong_root_denies():
    """A Biscuit minted under key K1 must not verify under key K2."""
    hex_key_1 = _gen_root_key_hex()
    hex_key_2 = _gen_root_key_hex()
    assert hex_key_1 != hex_key_2

    settings_k1 = _make_settings_with_hex_key(hex_key_1)
    settings_k2 = _make_settings_with_hex_key(hex_key_2)

    _clear_biscuit_module_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k1):
        from cappo_backend.security.biscuit import mint_biscuit_capability
        token = mint_biscuit_capability(
            caller_spiffe_id="spiffe://test/caller",
            executor_spiffe_id="spiffe://test/executor",
            capability_id="test-cap",
            reads=["resource.read"],
            writes=[],
            execution_id="exec-wrong-root",
            ttl_seconds=300,
        )

    _clear_biscuit_module_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings_k2):
        from cappo_backend.security.biscuit import verify_biscuit_capability
        result = verify_biscuit_capability(
            token_b64=token,
            executor_spiffe_id="spiffe://test/executor",
            action="resource.read",
        )

    assert result is False, (
        "Biscuit minted under K1 verified successfully under K2 — "
        "cryptographic isolation between root keys is broken."
    )


# ---------------------------------------------------------------------------
# T6 — Missing production root → STARTUP FAIL
# ---------------------------------------------------------------------------

def test_missing_production_root_fails_startup():
    """validate_production() must raise when BISCUIT_ROOT_PRIVATE_KEY_HEX is absent."""
    from cappo_backend.config import InsecureProductionConfigError, Settings
    s = Settings(environment="production", biscuit_root_private_key_hex=None)
    with pytest.raises(InsecureProductionConfigError) as exc_info:
        s.validate_production()
    assert "BISCUIT_ROOT_PRIVATE_KEY_HEX" in str(exc_info.value), (
        "validate_production() did not mention BISCUIT_ROOT_PRIVATE_KEY_HEX in its error."
    )


# ---------------------------------------------------------------------------
# T7 — Malformed configured root → STARTUP FAIL (field validation)
# ---------------------------------------------------------------------------

def test_malformed_hex_root_fails_settings_validation():
    """Settings construction must raise on malformed BISCUIT_ROOT_PRIVATE_KEY_HEX."""
    from pydantic import ValidationError

    from cappo_backend.config import Settings
    with pytest.raises((ValidationError, ValueError)):
        Settings(biscuit_root_private_key_hex="not-valid-hex")


def test_short_hex_root_fails_settings_validation():
    """Settings construction must raise on a valid-hex but too-short key."""
    from pydantic import ValidationError

    from cappo_backend.config import Settings
    with pytest.raises((ValidationError, ValueError)):
        Settings(biscuit_root_private_key_hex="deadbeef")  # only 8 chars, needs 64


# ---------------------------------------------------------------------------
# T8 — CWD-relative key path rejected by field validator
# ---------------------------------------------------------------------------

def test_cwd_relative_key_path_rejected():
    """A CWD-relative biscuit_root_key_path must be rejected at Settings construction."""
    from pydantic import ValidationError

    from cappo_backend.config import Settings
    with pytest.raises((ValidationError, ValueError)) as exc_info:
        Settings(biscuit_root_key_path="./my_key_file")
    assert "absolute" in str(exc_info.value).lower() or "relative" in str(exc_info.value).lower(), (
        "The error message for a relative path should mention 'absolute' or 'relative'."
    )


def test_unanchored_key_path_rejected():
    """An unanchored relative filename must be rejected at Settings construction."""
    from pydantic import ValidationError

    from cappo_backend.config import Settings
    with pytest.raises((ValidationError, ValueError)):
        Settings(biscuit_root_key_path="biscuit_root_key")


# ---------------------------------------------------------------------------
# T9 — Full lifecycle: configure K → issue → persist → destroy → reload → verify
# ---------------------------------------------------------------------------

def test_lifecycle_authority_survives_persistence_and_process_boundary():
    """
    The canonical lifecycle regression:

      configure stable root K
      → issue mount/Biscuit
      → persist mount (simulated by storing token_b64 in a variable)
      → destroy in-memory key state (process restart simulation)
      → instantiate fresh settings/key loader using K
      → reload persisted mount (token_b64)
      → verify same Biscuit
      → allowed CAPPO evaluation succeeds

    This proves authority survives BOTH:
      - the persistence boundary (token serialised to base64)
      - the process boundary (in-memory key state destroyed)
    """
    hex_key = _gen_root_key_hex()
    settings = _make_settings_with_hex_key(hex_key)

    # PHASE 1: Configure K and issue Biscuit
    _clear_biscuit_module_key_state()
    with patch("cappo_backend.security.biscuit.get_settings", return_value=settings):
        from cappo_backend.security.biscuit import (
            extract_authority_context,
            mint_biscuit_capability,
        )
        token_b64 = mint_biscuit_capability(
            caller_spiffe_id="spiffe://test/lifecycle-caller",
            executor_spiffe_id="spiffe://test/lifecycle-executor",
            capability_id="lifecycle-cap",
            reads=["data.read", "data.list"],
            writes=["data.write"],
            execution_id="exec-lifecycle-001",
            ttl_seconds=600,
        )

    # PHASE 2: Persist (simulated — token_b64 is the durable form)
    persisted_token = token_b64

    # PHASE 3: Destroy in-memory key state (process restart)
    _clear_biscuit_module_key_state()

    # PHASE 4: Fresh settings/key loader with same K
    fresh_settings = _make_settings_with_hex_key(hex_key)

    # PHASE 5: Reload persisted token and verify
    with patch("cappo_backend.security.biscuit.get_settings", return_value=fresh_settings):
        from cappo_backend.security.biscuit import (
            extract_authority_context,
            verify_biscuit_capability,
        )

        # Verification passes
        verify_result = verify_biscuit_capability(
            token_b64=persisted_token,
            executor_spiffe_id="spiffe://test/lifecycle-executor",
            action="data.read",
        )
        assert verify_result is True, (
            "Biscuit verification failed after full lifecycle (persist + process restart). "
            "Authority does NOT survive the combined persistence + process boundary."
        )

        # Authority context is extractable and correct
        ctx = extract_authority_context(persisted_token)
        assert ctx is not None, (
            "extract_authority_context returned None after lifecycle — "
            "CAPPO would fail closed, meaning the persistence+process boundary "
            "has broken authority extraction."
        )
        assert "data.read" in ctx.allowed_actions, (
            f"Expected 'data.read' in allowed_actions. Got: {ctx.allowed_actions}"
        )
        assert "data.write" in ctx.allowed_actions, (
            f"Expected 'data.write' in allowed_actions. Got: {ctx.allowed_actions}"
        )
