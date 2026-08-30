from __future__ import annotations

import pytest

from cappo_backend.config import InsecureProductionConfigError, Settings


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+psycopg://user:password@db/cappo",
        "cappo_require_persistent_pgl": True,
        "ei_signing_key": "11" * 32,
        "evidence_root_private_key_hex": "22" * 32,
        "cors_allow_origins": "https://veklom.com",
        "auth_enabled": True,
        "api_keys": "production-test-api-key",
        "license_admin_key": "production-test-license-key",
        "capi_gatekeeper_public_key": "production-test-capi-public-key",
        "approval_token_signing_key": "production-test-approval-key",
        "runtime_kind": "amphoteric",
        "runtime_instance": "cappo-production-test",
        "vault_master_key": "v" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_explicit_stable_evidence_root() -> None:
    settings = _production_settings(evidence_root_private_key_hex="")
    with pytest.raises(InsecureProductionConfigError, match="EVIDENCE_ROOT_PRIVATE_KEY_HEX"):
        settings.validate_production()


def test_production_rejects_wrong_length_evidence_root() -> None:
    settings = _production_settings(evidence_root_private_key_hex="22" * 31)
    with pytest.raises(InsecureProductionConfigError, match="exactly 64 hexadecimal"):
        settings.validate_production()


def test_production_rejects_non_hex_evidence_root() -> None:
    settings = _production_settings(evidence_root_private_key_hex="z" * 64)
    with pytest.raises(InsecureProductionConfigError, match="valid hexadecimal"):
        settings.validate_production()


def test_production_accepts_stable_32_byte_evidence_root() -> None:
    _production_settings().validate_production()


def test_development_can_retain_local_evidence_key_fallback() -> None:
    Settings(environment="development", evidence_root_private_key_hex="").validate_production()
