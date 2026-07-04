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
    ei_signing_provider: str = "hmac"

    # --- PGL ledger (gnomledger) forwarding ---
    # When PGL_LEDGER_URL is set, every governance event is mirrored into
    # gnomledger's append-only, hash-chained ledger.
    pgl_ledger_url: str | None = None
    pgl_ledger_api_key: str | None = None
    pgl_ledger_timeout_ms: int = 8000

    # --- Phase 6 execution bridge (Veklom BYOS MCP gateway) ---
    byos_mcp_gateway_url: str | None = None
    byos_internal_api_key: str | None = None
    covenant_exec_timeout_ms: int = 10000

    # --- Authority limits ---
    max_delegation_depth: int = 4

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
    # Comma-separated set of accepted API keys.
    api_keys: str = ""

    # --- License Server (this service acts as license authority) ---
    license_admin_key: str = ""  # Shared secret for /v1/license admin endpoints
    # Set the same value in veklom-byos-backend as LICENSE_ADMIN_KEY

    # --- Veklom BYOS Backend (Real PGL) ---
    veklom_byos_backend_url: str | None = None  # https://api.veklom.com/v1
    veklom_api_key: str | None = None  # API key for veklom-byos-backend

    # --- Execution layer (real provider + circuit breaker) ---
    # "echo" uses the deterministic stub (default; tests/local dev). "openai"
    # wires the OpenAI-compatible HTTP client (OpenAI / Groq / Ollama).
    executor_mode: str = "echo"
    # Primary provider.
    llm_provider_name: str = "ollama"
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen2.5:0.5b"
    llm_api_key: str = ""
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
        if not self.auth_enabled:
            problems.append(
                "AUTH_ENABLED must be true in production (every non-public route "
                "requires authentication)."
            )
        elif not self.api_key_set:
            problems.append(
                "API_KEYS must contain at least one key when AUTH_ENABLED is true."
            )
        if not self.license_admin_key:
            problems.append(
                "LICENSE_ADMIN_KEY must be set in production to secure admin endpoints."
            )

        if problems:
            raise InsecureProductionConfigError(
                "Refusing to start with insecure production configuration: "
                + " ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()

