"""Shared test fixtures — in-memory SQLite, settings override, client."""

from __future__ import annotations

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
    import cappo_backend.models  # noqa: F401 — register models
    Base.metadata.create_all(_test_engine)
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
    )



@pytest.fixture
def client(db: Session, settings: Settings) -> TestClient:
    """TestClient with DI overrides for db session and settings."""
    def _override_session() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_session] = _override_session
    from cappo_backend.config import get_settings as _gs
    from cappo_backend.main import create_app
    
    # Create a fresh app instance with test settings so middlewares get the right config
    test_app = create_app(settings)
    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[_gs] = lambda: settings
    
    # Provide default auth header to avoid breaking existing tests
    test_client = TestClient(test_app)
    api_key = next(iter(settings.api_key_set)) if settings.api_key_set else "test-key"
    test_client.headers.update({
        "X-API-Key": api_key,
        "X-Wallet-Address": "test-wallet"
    })
    yield test_client
    test_app.dependency_overrides.clear()

