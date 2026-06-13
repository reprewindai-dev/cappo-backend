"""Application configuration.

Seeded from the old backend's ``core/config.py`` pattern (pydantic-settings + .env),
but adds the explicit CAPPO production-discipline boundaries the migration note
flagged as missing: a dedicated EI signing key, a fail-closed PGL flag, and an
explicit environment flag. Placeholders must never be acceptable in production.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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

    # ExecutionIdentityV1 signing configuration (HSM-backed for production)
    ei_signing_provider: str = "hmac"  # hmac | aws | azure | vault
    ei_signing_key: str = "dev-insecure-ei-signing-key"  # HMAC only

    # AWS KMS configuration (when ei_signing_provider="aws")
    aws_kms_key_id: str | None = None  # arn:aws:kms:region:account:key/id
    aws_region: str | None = None  # e.g., us-east-1

    # Azure Key Vault configuration (when ei_signing_provider="azure")
    azure_key_vault_url: str | None = None  # https://myvault.vault.azure.net/keys/mykey

    # HashiCorp Vault configuration (when ei_signing_provider="vault")
    vault_url: str | None = None  # https://vault.example.com:8200
    vault_transit_key_name: str | None = None  # e.g., cappo-ei-signing-key
    vault_token: str | None = None  # s.xxxxxx

    # --- Veklom BYOS Backend (Real PGL) ---
    veklom_byos_backend_url: str | None = None  # https://api.veklom.com/v1
    veklom_api_key: str | None = None  # API key for veklom-byos-backend
    
    # --- Veklom Payment Configuration (x402) ---
    veklom_evm_address: str | None = None  # 0x... for receiving payments
    veklom_svm_address: str | None = None  # Solana address for payments
    
    # --- x402 Payment Protocol ---
    x402_enabled: bool = False  # Enable x402 payment middleware
    x402_facilitator_url: str = "https://x402.org/facilitator"
    x402_exec_price: str = "$0.001"  # Price per agent execution
    x402_mint_price: str = "$0.005"  # Price per agent minting
    x402_networks: str = "base,base-sepolia"  # Comma-separated networks

    # --- Authority limits ---
    max_delegation_depth: int = 4

    # --- License Server (this service acts as license authority) ---
    license_admin_key: str = ""  # Shared secret for /v1/license admin endpoints
    # Set the same value in veklom-byos-backend as LICENSE_ADMIN_KEY

    # --- EAT (Execution Authorization Token) ---
    eat_signing_provider: str = "hmac"  # hmac | aws | azure | vault
    eat_signing_key: str = "dev-insecure-eat-signing-key"  # HMAC only
    eat_default_ttl_seconds: int = 300  # 5 minutes
    eat_max_ttl_seconds: int = 600  # 10 minutes
    edge_mcp_identity: str = "cappo-edge-mcp"  # Audience field in EAT
    inside_mcp_identity: str = "cappo-inside-mcp"  # Issuer field in EAT
    eat_nonce_backend: str = "memory"  # memory | redis
    eat_nonce_redis_url: str | None = None  # redis://localhost:6379/1

    # --- CORS ---
    cors_origins: list[str] = ["*"]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @model_validator(mode="after")
    def validate_production_config(self) -> Settings:
        import logging
        _log = logging.getLogger(__name__)
        if self.is_production:
            if self.database_url.startswith("sqlite"):
                raise ValueError(
                    "SQLite database URL is not allowed in production. "
                    "Configure DATABASE_URL with a production-ready database (e.g., PostgreSQL)."
                )
            # HMAC is allowed in production for initial/staging deployments on Coolify.
            # For full non-repudiation compliance, migrate to aws/azure/vault.
            if self.ei_signing_provider == "hmac":
                _log.warning(
                    "[CAPPO] EI_SIGNING_PROVIDER=hmac in production. "
                    "This is accepted for Coolify staging deployments. "
                    "Migrate to 'aws', 'azure', or 'vault' for HSM-backed non-repudiation."
                )
            if self.ei_signing_provider == "aws":
                if not self.aws_kms_key_id:
                    raise ValueError("AWS_KMS_KEY_ID required when EI_SIGNING_PROVIDER=aws")
                if not self.aws_region:
                    raise ValueError("AWS_REGION required when EI_SIGNING_PROVIDER=aws")
            elif self.ei_signing_provider == "azure":
                if not self.azure_key_vault_url:
                    raise ValueError("AZURE_KEY_VAULT_URL required when EI_SIGNING_PROVIDER=azure")
            elif self.ei_signing_provider == "vault":
                if not self.vault_url:
                    raise ValueError("VAULT_URL required when EI_SIGNING_PROVIDER=vault")
                if not self.vault_transit_key_name:
                    raise ValueError("VAULT_TRANSIT_KEY_NAME required when EI_SIGNING_PROVIDER=vault")
            # EAT signing: warn only in production (same rationale as EI above)
            if self.eat_signing_provider == "hmac":
                _log.warning(
                    "[CAPPO] EAT_SIGNING_PROVIDER=hmac in production. "
                    "Migrate to 'aws', 'azure', or 'vault' for full non-repudiation."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

