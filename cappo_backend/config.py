"""Application configuration.

Seeded from the old backend's ``core/config.py`` pattern (pydantic-settings + .env),
but adds the explicit CAPPO production-discipline boundaries the migration note
flagged as missing: a dedicated EI signing key, a fail-closed PGL flag, and an
explicit environment flag. Placeholders must never be acceptable in production.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# The development default for the EI signing key. It is intentionally obvious so
# that production refuses to boot with it (see ``validate_production``).
INSECURE_EI_SIGNING_KEY = "dev-insecure-ei-signing-key"
MIN_EI_SIGNING_KEY_LEN = 16


class InsecureProductionConfigError(RuntimeError):
    """Raised at startup when production config still carries unsafe defaults."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Environment ---
    environment: str = "development"

    # --- Database ---
    database_url: str = "sqlite+pysqlite:///./cappo.db"

    # --- LAW 0 / governance discipline ---
    # When true, a missing DB session is fatal: no simulated/non-persisted PGL
    # certificate may be accepted. Production must set this to true.
    cappo_require_persistent_pgl: bool = False

    # Signing key for ExecutionIdentityV1. Must be overridden in production.
    ei_signing_key: str = INSECURE_EI_SIGNING_KEY

    # --- Authority limits ---
    max_delegation_depth: int = 4

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def validate_production(self) -> None:
        """Fail-closed: refuse to run production with unsafe defaults.

        Called at application startup. In non-production environments this is a
        no-op so local development stays frictionless. In production it raises
        :class:`InsecureProductionConfigError` if any governance-critical setting
        is still at an insecure default — there is no silent degradation.
        """
        if not self.is_production:
            return

        problems: list[str] = []
        if self.ei_signing_key == INSECURE_EI_SIGNING_KEY:
            problems.append(
                "EI_SIGNING_KEY is still the insecure development default; "
                "set a strong unique key in production."
            )
        elif len(self.ei_signing_key) < MIN_EI_SIGNING_KEY_LEN:
            problems.append(
                f"EI_SIGNING_KEY must be at least {MIN_EI_SIGNING_KEY_LEN} characters "
                "in production."
            )
        if not self.cappo_require_persistent_pgl:
            problems.append(
                "CAPPO_REQUIRE_PERSISTENT_PGL must be true in production "
                "(no simulated/non-persisted PGL certificates allowed)."
            )
        if self.database_url.lower().startswith("sqlite"):
            problems.append(
                "DATABASE_URL must point to a production-grade database "
                "(SQLite is not permitted in production)."
            )

        if problems:
            raise InsecureProductionConfigError(
                "Refusing to start with insecure production configuration: "
                + " ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
