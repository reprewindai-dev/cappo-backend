from __future__ import annotations

import stat
from pathlib import Path

import pytest
from biscuit_auth import KeyPair

import cappo_backend.security.biscuit as biscuit
from cappo_backend.config import InsecureProductionConfigError, Settings


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str = "test",
    private_key_hex: str | None = None,
    key_path: Path | None = None,
) -> Settings:
    settings_kwargs: dict[str, object] = {
        "environment": environment,
        "biscuit_root_key_path": str(key_path or Path(".biscuit_root_key")),
    }
    if private_key_hex is not None:
        settings_kwargs["biscuit_root_private_key_hex"] = private_key_hex
    settings = Settings(**settings_kwargs)
    monkeypatch.setattr(biscuit, "get_settings", lambda: settings)
    biscuit._ROOT_KEY_PAIR = None
    return settings


def _mint_token() -> str:
    return biscuit.mint_biscuit_capability(
        caller_spiffe_id="spiffe://example.org/caller",
        executor_spiffe_id="spiffe://example.org/executor",
        capability_id="records@v1",
        reads=["record.read"],
        writes=["record.create"],
        execution_id="execution-1",
        ttl_seconds=60,
    )


def test_configured_hex_key_is_stable_across_loads_and_extracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = KeyPair()
    monkeypatch.setenv(
        "BISCUIT_ROOT_PRIVATE_KEY_HEX",
        configured.private_key.to_bytes().hex(),
    )
    _settings(
        monkeypatch,
    )

    first = biscuit.get_root_key_pair()
    biscuit._ROOT_KEY_PAIR = None
    second = biscuit.get_root_key_pair()
    assert first.public_key.to_bytes() == second.public_key.to_bytes()

    token = _mint_token()
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    biscuit._ROOT_KEY_PAIR = None
    authority = biscuit.extract_authority_context(token)
    assert authority is not None
    assert authority.allowed_actions == {"record.read", "record.create"}


def test_absolute_file_fallback_survives_simulated_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "state" / "biscuit-root-key"
    _settings(monkeypatch, key_path=key_path)
    first_directory = tmp_path / "first"
    first_directory.mkdir()
    monkeypatch.chdir(first_directory)

    token = _mint_token()
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    other_directory = tmp_path / "other"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)
    biscuit._ROOT_KEY_PAIR = None
    authority = biscuit.extract_authority_context(token)
    assert authority is not None
    assert authority.allowed_actions == {"record.read", "record.create"}


def test_rotated_root_key_fails_closed_during_authority_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "biscuit-root-key"
    _settings(monkeypatch, key_path=key_path)
    token = _mint_token()
    key_path.unlink()
    biscuit._ROOT_KEY_PAIR = None

    authority = biscuit.extract_authority_context(token)
    assert authority is None


def test_file_creation_race_loads_existing_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "biscuit-root-key"
    existing = KeyPair()
    key_path.write_bytes(existing.private_key.to_bytes())
    _settings(monkeypatch, key_path=key_path)

    original_exists = biscuit.os.path.exists
    reported_missing = False

    def report_missing_once(path: object) -> bool:
        nonlocal reported_missing
        if not reported_missing:
            reported_missing = True
            return False
        return original_exists(path)

    monkeypatch.setattr(biscuit.os.path, "exists", report_missing_once)
    loaded = biscuit.get_root_key_pair()

    assert loaded.public_key.to_bytes() == existing.public_key.to_bytes()


def test_production_without_configured_root_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BISCUIT_ROOT_PRIVATE_KEY_HEX", raising=False)
    settings = _settings(monkeypatch, environment="production")

    with pytest.raises(InsecureProductionConfigError, match="BISCUIT_ROOT_PRIVATE_KEY_HEX"):
        settings.validate_production()

    biscuit._ROOT_KEY_PAIR = None
    with pytest.raises(InsecureProductionConfigError, match="BISCUIT_ROOT_PRIVATE_KEY_HEX"):
        biscuit.get_root_key_pair()
