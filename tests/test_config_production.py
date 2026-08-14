"""Tests for fail-closed production config validation (GAP: environment discipline).

``Settings.validate_production`` must be a no-op in dev/test but refuse to start
production with any insecure default.
"""

from __future__ import annotations

import pytest

from cappo_backend.config import (
    INSECURE_EI_SIGNING_KEY,
    InsecureProductionConfigError,
    Settings,
)

_SECURE_KEY = "ab" * 24
_PG_URL = "postgresql+psycopg://u:p@db:5432/cappo"
_COMPROMISED_API_KEY = "cappo_internal_exec_key_veklom_2026"


def _prod(**overrides) -> Settings:
    base = dict(
        environment="production",
        ei_signing_key=_SECURE_KEY,
        cappo_require_persistent_pgl=True,
        database_url=_PG_URL,
        auth_enabled=True,
        api_keys="prod-key-1",
        license_admin_key="prod-license-key",
        capi_gatekeeper_public_key="test-capi-public-key",
        approval_token_signing_key="test-approval-token-key",
        jwt_public_verification_key="test-jwt-key",
        cors_allow_origins="https://api.veklom.com",
    )
    base.update(overrides)
    return Settings(**base)


class TestNonProduction:
    def test_dev_defaults_are_noop(self) -> None:
        # Insecure defaults are fine outside production.
        Settings(environment="development").validate_production()

    def test_test_env_is_noop(self) -> None:
        Settings(environment="test").validate_production()


class TestApiKeyValidation:
    def test_exact_compromised_api_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="known compromised/default credential"):
            Settings(api_keys=_COMPROMISED_API_KEY)

    def test_compromised_api_key_in_comma_separated_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="known compromised/default credential"):
            Settings(api_keys=f"safe-key,{_COMPROMISED_API_KEY},other-safe-key")

    def test_safe_api_keys_accepted(self) -> None:
        settings = Settings(api_keys="safe-key-1,safe-key-2")
        assert settings.api_key_set == frozenset({"safe-key-1", "safe-key-2"})


class TestProductionFailClosed:
    def test_fully_valid_prod_passes(self) -> None:
        _prod().validate_production()  # should not raise

    def test_insecure_signing_key_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="EI_SIGNING_KEY"):
            _prod(ei_signing_key=INSECURE_EI_SIGNING_KEY).validate_production()

    def test_short_signing_key_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="at least"):
            _prod(ei_signing_key="short").validate_production()

    def test_non_persistent_pgl_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="CAPPO_REQUIRE_PERSISTENT_PGL"):
            _prod(cappo_require_persistent_pgl=False).validate_production()

    def test_sqlite_rejected_in_prod(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="DATABASE_URL"):
            _prod(database_url="sqlite+pysqlite:///./cappo.db").validate_production()

    def test_auth_disabled_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="AUTH_ENABLED"):
            _prod(auth_enabled=False).validate_production()

    def test_auth_enabled_without_keys_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="API_KEYS"):
            _prod(api_keys="").validate_production()

    def test_missing_capi_gatekeeper_key_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="CAPI_GATEKEEPER_PUBLIC_KEY"):
            _prod(capi_gatekeeper_public_key="").validate_production()

    def test_missing_approval_token_signing_key_rejected(self) -> None:
        with pytest.raises(InsecureProductionConfigError, match="APPROVAL_TOKEN_SIGNING_KEY"):
            _prod(approval_token_signing_key="").validate_production()

    def test_multiple_problems_aggregated(self) -> None:
        with pytest.raises(InsecureProductionConfigError) as exc:
            Settings(
                environment="production",
                ei_signing_key=INSECURE_EI_SIGNING_KEY,
                cappo_require_persistent_pgl=False,
                database_url="sqlite+pysqlite:///./cappo.db",
            ).validate_production()
        msg = str(exc.value)
        assert "EI_SIGNING_KEY" in msg
        assert "CAPPO_REQUIRE_PERSISTENT_PGL" in msg
        assert "DATABASE_URL" in msg
