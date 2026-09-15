"""Shared test fixtures — in-memory SQLite, settings override, client."""

from __future__ import annotations
import os
os.environ["CAPPO_EXECUTION_MODE"] = "test"

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cappo_backend.config import Settings
from cappo_backend.db.base import Base
from cappo_backend.db.session import get_session
from cappo_backend.main import app

_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(bind=_test_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _create_tables() -> Iterator[None]:
    import cappo_backend.models  # noqa: F401 — register models on Base
    Base.metadata.create_all(_test_engine)
    # Seed the merkle_leaf_sequence singleton row (id=1, next_value=0).
    # This mirrors what the production migration does.  Must not reset on an
    # existing DB with committed receipts — safe here because the table is
    # re-created fresh for every test.
    from sqlalchemy import text
    with _test_engine.connect() as conn:
        conn.execute(
            text("INSERT OR IGNORE INTO merkle_leaf_sequence (id, next_value) VALUES (1, 0)")
        )
        conn.commit()
    yield
    Base.metadata.drop_all(_test_engine)


@pytest.fixture
def db() -> Iterator[Session]:
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        ei_signing_key="test-signing-key",
        cappo_require_persistent_pgl=False,
        cappo_internal_api_keys="test-key",
        environment="test",
        auth_enabled=False,
        api_keys="test-key",
        veklom_byos_backend_url=None,
        executor_mode="echo",
        runtime_kind="amphoteric",
        runtime_instance="test-runtime",
    )


@pytest.fixture
def prod_settings() -> Settings:
    return Settings(
        database_url="postgresql+psycopg2://localhost/cappodb",
        ei_signing_key="test-signing-key",
        ei_signing_provider="aws",
        aws_kms_key_id="arn:aws:kms:us-east-1:123456789:key/test",
        aws_region="us-east-1",
        eat_signing_provider="aws",
        cappo_require_persistent_pgl=True,
        environment="production",
        veklom_byos_backend_url=None,
        runtime_kind="amphoteric",
        runtime_instance="prod-runtime",
    )



@pytest.fixture
def client(db: Session, settings: Settings) -> TestClient:
    """TestClient with DI overrides for db session and settings."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class InjectWorkspaceMiddleware(BaseHTTPMiddleware):
        """Pre-inject auth_workspace so exec/revocation/ledger endpoints have the
        workspace context they require.  The workspace is taken from the
        X-Workspace-ID header when present (so capability-mount ownership tests
        work correctly); otherwise it falls back to the test sentinel value.
        auth_principal is set as a fallback only — AuthMiddleware will overwrite
        it when auth_enabled=True so real key-fingerprint ownership tests still
        distinguish between callers."""

        async def dispatch(self, request, call_next):
            if "X-No-Workspace" not in request.headers:
                workspace = request.headers.get("X-Workspace-ID") or "test-workspace"
                request.scope["auth_workspace"] = workspace
            if "auth_principal" not in request.scope:
                request.scope["auth_principal"] = "test:principal"
            if "caller_spiffe_id" not in request.scope:
                request.scope["caller_spiffe_id"] = "spiffe://example.org/workload/cappo-backend"
            return await call_next(request)

    def _override_session() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_session] = _override_session
    from cappo_backend.config import get_settings as _gs
    from cappo_backend.main import create_app

    # Create a fresh app instance with test settings so middlewares get the right config
    test_app = create_app(settings)
    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[_gs] = lambda: settings

    # Add workspace injection outermost so it runs before AuthMiddleware.
    # AuthMiddleware will overwrite auth_principal (not auth_workspace) when
    # auth_enabled=True, preserving the ownership-binding test semantics.
    test_app.add_middleware(InjectWorkspaceMiddleware)

    # Provide default auth header to avoid breaking existing tests
    test_client = TestClient(test_app)
    api_key = next(iter(settings.api_key_set)) if settings.api_key_set else "test-key"
    test_client.headers.update({
        "X-API-Key": api_key,
        "X-Wallet-Address": "test-wallet"
    })
    yield test_client
    test_app.dependency_overrides.clear()



from cappo_backend.services.active_verification import registry, VerifierModule, VerifiedRuntimeContext
import time

class UniversalMockVerifier(VerifierModule):
    is_mock = True
    def discover(self, connection_info: dict) -> bool:
        return True
    def challenge(self, connection_info: dict) -> str:
        return 'mock_nonce'
    def measure(self, connection_info: dict, challenge_nonce: str) -> dict:
        connection_info['nonce'] = challenge_nonce
        return connection_info
    def verify(self, measurement: dict, challenge_nonce: str) -> bool:
        return measurement.get('nonce') == challenge_nonce
    def attest(self, measurement: dict) -> VerifiedRuntimeContext:
        return VerifiedRuntimeContext(
            substrate_kind=measurement.get('substrate_hint', 'test_kind'),
            substrate_instance_id=measurement.get('instance_hint', 'test_instance'),
            boot_instance_id='test_boot_id',
            runtime_identity='test_identity',
            verifier_method='universal_mock',
            verifier_identity='test_verifier',
            authority_epoch=1,
            package_digest='test_digest',
            state_root='test_root',
            challenge_nonce=measurement.get('nonce', 'mock_nonce'),
            observed_at=str(int(time.time())),
            expires_at=str(int(time.time()) + 1000),
            measurement_digest='test_measurement',
            evidence_level='L0_MOCK'
        )

try:
    registry.register('universal_mock', UniversalMockVerifier())
except RuntimeError:
    pass
