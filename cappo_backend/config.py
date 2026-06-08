"""Application configuration.

Seeded from the old backend's ``core/config.py`` pattern (pydantic-settings + .env),
but adds the explicit CAPPO production-discipline boundaries the migration note
flagged as missing: a dedicated EI signing key, a fail-closed PGL flag, and an
explicit environment flag. Placeholders must never be acceptable in production.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ei_signing_key: str = "dev-insecure-ei-signing-key"

    # --- Authority limits ---
    max_delegation_depth: int = 4

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
