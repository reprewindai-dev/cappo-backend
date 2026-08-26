"""Application configuration.

Seeded from the old backend's ``core/config.py`` pattern (pydantic-settings + .env),
but adds the explicit CAPPO production-discipline boundaries the migration note
flagged as missing: a dedicated EI signing key, a fail-closed PGL flag, and an
explicit environment flag. Placeholders must never be acceptable in production.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The development default for the EI signing key. It is intentionally obvious so
# that production refuses to boot with it (see ``validate_production``).
INSECURE_EI_SIGNING_KEY = "dev-insecure-ei-signing-key"
MIN_EI_SIGNING_KEY_LEN = 48


class InsecureProductionConfigError(RuntimeError):
    """Raised at startup when production config still carries unsafe defaults."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Environment ---
    environment: str = "development"

    # Gnomledger Canonical PGL
    gnomledger_url: str | None = None
    gnomledger_api_key: str | None = None
    cappo_allow_noncanonical_pgl_fallback: bool = False

    # --- Database ---
    database_url: str = "sqlite+pysqlite:///./cappo.db"

    # --- LAW 0 / governance discipline ---
    # When true, a missing DB session is fatal: no simulated/non-persisted PGL
    # certificate may be accepted. Production must set this to true.
    cappo_require_persistent_pgl: bool = False

    # Signing key for ExecutionIdentityV1. Must be overridden in production.
    ei_signing_key: str = INSECURE_EI_SIGNING_KEY
    ei_signing_provider: str = "ed25519"

    # --- PGL ledger (gnomledger) forwarding ---
    # When PGL_LEDGER_URL is set, every governance event is mirrored into
    # gnomledger's append-only, hash-chained ledger.
    pgl_ledger_url: str | None = None
    pgl_ledger_api_key: str | None = None
    pgl_ledger_timeout_ms: int = 8000

    # Capability discovery and signed beacon publication.
    capability_packages_json: str | None = None
    capability_beacon_issuer: str = "https://cappo.veklom.com"
    capability_beacon_ttl_seconds: int = 300
    capability_beacon_kid: str = "default"
    capability_beacon_keys_json: str | None = None

    # --- Phase 6 execution bridge (Veklom BYOS MCP gateway) ---
    byos_mcp_gateway_url: str | None = None
    byos_internal_api_key: str | None = None
    covenant_exec_timeout_ms: int = 10000

    # --- Authority limits ---
    max_delegation_depth: int = 4

    # --- Consequence-bearing runtime ownership ---
    # These values identify this independently deployed runtime control service.
    # They are minted into the signed execution identity by CAPPO; callers never
    # choose or override path ownership.
    runtime_kind: str = ""
    runtime_instance: str = ""

    # --- Observability / CORS ---
    # Comma-separated list of allowed CORS origins. Default "*" suits a headless
    # API in dev; production should pin explicit origins.
    cors_allow_origins: str = "*"
    log_level: str = "INFO"

    # --- Authentication (separate, earlier layer than LAW 0 authority) ---
    # When true, every non-public route requires a valid X-API-Key. This is
    # authentication only; it never substitutes for EI authority (auth != authority).
    # Disabled by default for local dev; production must enable it.
    auth_enabled: bool = False
    # Comma-separated set of accepted API keys. Deployment must inject values;
    # repository defaults intentionally contain no credential material.
    api_keys: str = ""

    # --- JWT Authentication ---
    jwt_auth_enabled: bool = False
    jwt_public_verification_key: str = ""
    jwt_algorithm: str = "EdDSA"
    jwt_issuer: str = ""
    jwt_audience: str = ""

    # --- License Server (this service acts as license authority) ---
    license_admin_key: str = ""  # Shared secret for /v1/license admin endpoints
    # Set the same value in veklom-byos-backend as LICENSE_ADMIN_KEY

    # --- Veklom BYOS Backend (Real PGL) ---
    veklom_byos_backend_url: str | None = None  # https://api.veklom.com/v1
    veklom_api_key: str | None = None  # API key for veklom-byos-backend

    # --- Universal USB (cAPI) Integration ---
    capi_backend_url: str | None = "http://capi-container:3003"
    capi_api_key: str | None = None

    capi_external_validation_enabled: bool = False
    # Public verification key used by the local cAPI gatekeeper for signed
    # request envelopes. Production /v1/exec requests must be signed.
    capi_gatekeeper_public_key: str = ""
    # HMAC verification key for bound human-approval resume tokens. Production
    # approval-gated execution must never accept placeholder signatures.
    approval_token_signing_key: str = ""

    # --- Execution layer (real provider + circuit breaker) ---
    # "echo" uses the deterministic stub (default; tests/local dev). "openai"
    # wires the OpenAI-compatible HTTP client (OpenAI / Groq / Ollama).
    executor_mode: str = "echo"
    # Primary provider.
    llm_provider_name: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen2.5:0.5b"
    llm_api_key: str = ""
    allow_legacy_global_provider_config: bool = False

    # P0-6 Local Ollama topology settings
    local_ollama_enabled: bool = False
    ollama_upstream_url: str = ""
    ollama_keep_alive: int = 300

    # Master key for encrypting/decrypting BYOK tenant credentials.
    # Must be set in production to a strong, unique value.
    vault_master_key: str = "dev-insecure-vault-master-key-change-me"
    
    # Optional fallback provider; enabled only when a fallback base_url is set.
    llm_fallback_provider_name: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = ""
    llm_timeout_seconds: float = 30.0
    # Circuit-breaker tuning (applied per provider).
    breaker_failure_threshold: int = 3
    breaker_recovery_timeout: float = 30.0
    breaker_success_threshold: int = 1

    # Ed25519 public key used to verify signed 503 responses before a
    # configured fallback provider may be considered.
    vnp_federation_public_key: str = ""

    # --- Completion cache (latency) ---
    # When true, a tiered hot (in-process) + warm (shared) cache fronts the
    # providers. Cache hits short-circuit the provider call only; the governed
    # pipeline (PGL/EI/LAW 0) still runs on every request.
    cache_enabled: bool = False
    cache_ttl_seconds: int = 300
    cache_namespace: str = "cappo"
    hot_cache_max_size: int = 1024
    # Warm tier backend: "memory" (process-local; dev/test), "redis" (TCP/TLS,
    # self-hosted or managed), or "upstash" (REST).
    cache_warm_backend: str = "memory"
    # Used when cache_warm_backend="redis" (redis:// or rediss:// for TLS).
    redis_url: str = ""
    # Used when cache_warm_backend="upstash".
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""

    # --- SPIFFE / mTLS Enforcement ---
    enforce_spiffe: bool = False
    spiffe_trust_domain: str = "example.org"

    # --- Distributed limits ---
    max_runs_per_hour: int = 1000
    max_tokens_per_hour: int = 10000000
    max_node_concurrent_runs: int = 50

    # --- x402 Payment Gateway ---
    # Treasury wallet address for receiving USDC payments.
    veklom_evm_address: str = ""
    # x402 facilitator URL (default is Coinbase public facilitator).
    x402_facilitator_url: str = "https://x402.org/facilitator"
    # Pricing in USD (string format, e.g. "$0.001").
    x402_exec_price: str = "$0.001"
    x402_mint_price: str = "$0.005"
    # Comma-separated list of enabled EVM networks.
    x402_networks: str = "base,base-sepolia"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @field_validator("api_keys", mode="before")
    @classmethod
    def reject_known_insecure_keys(cls, v: Any) -> Any:
        if isinstance(v, str):
            import hashlib
            import secrets
            import os
            
            _KNOWN_COMPROMISED_KEY_FINGERPRINT = "d2623fa3f2c01611397de54f7724fbe483a53fbec78d46b76aa283dbe02600d8"
            keys = [k.strip() for k in v.split(",")]
            has_compromised = False
            for k in keys:
                if hashlib.sha256(k.encode()).hexdigest() == _KNOWN_COMPROMISED_KEY_FINGERPRINT:
                    has_compromised = True
                    break
                    
            if has_compromised:
                env = (os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development").lower()
                is_prod = env in {"production", "prod"}
                if is_prod:
                    raise InsecureProductionConfigError(
                        "Compromised CAPPO API credential detected. "
                        "Rotate the API_KEYS value in the Coolify environment panel and redeploy."
                    )
                else:
                    import logging
                    logging.warning(
                        "WARNING: Compromised CAPPO API credential detected in development. "
                        "Silently replacing with a random secure token."
                    )
                    new_keys = []
                    for k in keys:
                        if hashlib.sha256(k.encode()).hexdigest() == _KNOWN_COMPROMISED_KEY_FINGERPRINT:
                            new_keys.append(secrets.token_hex(32))
                        else:
                            new_keys.append(k)
                    return ",".join(new_keys)
        return v

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
                f"EI_SIGNING_KEY must be at least {MIN_EI_SIGNING_KEY_LEN} hex characters "
                "in production for Ed25519 signing."
            )
        else:
            try:
                bytes.fromhex(self.ei_signing_key)
            except ValueError:
                problems.append("EI_SIGNING_KEY must be a valid hex string.")
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
        if self.cors_allow_origins == "*" or not self.cors_allow_origins:
            problems.append(
                "CORS_ALLOW_ORIGINS must not be '*' in production. "
                "Pin explicit origins (e.g., https://veklom.com,https://api.veklom.com)."
            )
        if not self.auth_enabled:
            problems.append(
                "AUTH_ENABLED must be true in production (every non-public route "
                "requires authentication)."
            )
        elif not self.api_key_set:
            problems.append("API_KEYS must contain at least one key when AUTH_ENABLED is true.")
        if not self.license_admin_key:
            problems.append(
                "LICENSE_ADMIN_KEY must be set in production to secure admin endpoints."
            )
        if not self.capi_gatekeeper_public_key:
            problems.append(
                "CAPI_GATEKEEPER_PUBLIC_KEY must be set in production to verify signed cAPI envelopes."
            )
        if not self.approval_token_signing_key:
            problems.append(
                "APPROVAL_TOKEN_SIGNING_KEY must be set in production for approval-token verification."
            )
        if not self.runtime_kind.strip():
            problems.append(
                "RUNTIME_KIND must identify the deployed runtime control service in production."
            )
        if not self.runtime_instance.strip():
            problems.append(
                "RUNTIME_INSTANCE must identify this deployed runtime instance in production."
            )
        if self.jwt_auth_enabled:
            if not self.jwt_public_verification_key:
                problems.append(
                    "JWT_PUBLIC_VERIFICATION_KEY must be set in production when JWT_AUTH_ENABLED is true."
                )
            if not self.jwt_issuer:
                problems.append(
                    "JWT_ISSUER must be set in production when JWT_AUTH_ENABLED is true."
                )
            if not self.jwt_audience:
                problems.append(
                    "JWT_AUDIENCE must be set in production when JWT_AUTH_ENABLED is true."
                )

        if self.allow_legacy_global_provider_config:
            problems.append(
                "ALLOW_LEGACY_GLOBAL_PROVIDER_CONFIG must be false in production. "
                "Global provider fallback violates tenant isolation."
            )

        if self.llm_fallback_base_url and not self.vnp_federation_public_key:
            problems.append(
                "VNP_FEDERATION_PUBLIC_KEY must be set in production to verify 503 fallback signatures."
            )

        if self.vault_master_key == "dev-insecure-vault-master-key-change-me" or not self.vault_master_key.strip():
            problems.append(
                "VAULT_MASTER_KEY must be set to a strong, unique secret key in production."
            )
        elif len(self.vault_master_key.strip()) < 32:
            problems.append(
                "VAULT_MASTER_KEY must be at least 32 characters long in production."
            )

        if problems:
            raise InsecureProductionConfigError(
                "Refusing to start with insecure production configuration: " + " ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
