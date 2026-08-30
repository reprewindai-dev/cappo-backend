"""PostgreSQL integration tests to validate dialect compatibility and JSONB behavior."""

from __future__ import annotations

import os

import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed — skipping PostgreSQL integration tests")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from cappo_backend.db.base import Base  # noqa: E402
from cappo_backend.models.governed_run import GovernedRun  # noqa: E402

# Determine the test database URL. Default to a typical local development postgres container database.
POSTGRES_TEST_URL = os.getenv(
    "POSTGRES_TEST_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/cappo_test"
)


def is_postgres_available() -> bool:
    """Check if the PostgreSQL test database is accessible."""
    try:
        engine = create_engine(POSTGRES_TEST_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not is_postgres_available(),
    reason="PostgreSQL test database is not available. Start a PostgreSQL container and set POSTGRES_TEST_URL to enable.",
)


@pytest.fixture
def pg_session():
    """Yields a database session connected to PostgreSQL."""
    engine = create_engine(POSTGRES_TEST_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_postgres_jsonb_compatibility(pg_session) -> None:
    # 1. Create a run with a complex dict structure in hashes/scope/pgl_identity (which map to JSON/JSONB)
    run = GovernedRun(
        run_id="pg-test-run-123",
        workspace_id="default-ws",
        tenant_id="default-tenant",
        state="CREATED",
        hashes={"genome_hash": "abc", "constitution_hash": "xyz"},
        scope={"tools": ["llm.exec", "fs.read"], "limits": {"max_cost": 100}},
        pgl_identity={"pre_execution_certificate_id": "cert-123", "persisted": True},
    )

    pg_session.add(run)
    pg_session.commit()

    # 2. Query back the run and assert properties
    stmt = select(GovernedRun).where(GovernedRun.run_id == "pg-test-run-123")
    retrieved = pg_session.scalars(stmt).one()

    assert retrieved.hashes["genome_hash"] == "abc"
    assert "fs.read" in retrieved.scope["tools"]
    assert retrieved.pgl_identity["persisted"] is True

    # 3. Test querying using JSON path expressions (which verifies JSON/JSONB compilation on Postgres)
    # Filter by a nested JSON key
    stmt_json = select(GovernedRun).where(
        GovernedRun.pgl_identity["pre_execution_certificate_id"].as_string() == "cert-123"
    )
    json_result = pg_session.scalars(stmt_json).first()
    assert json_result is not None
    assert json_result.run_id == "pg-test-run-123"
