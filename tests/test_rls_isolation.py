import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager

from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
from cappo_backend.db.base import Base
from cappo_backend.config import get_settings

# These tests REQUIRE a PostgreSQL database with a non-owner user role.
# SQLite does not support RLS. They will be skipped if the dialect is not Postgres.

@pytest.fixture(scope="session")
def pg_engine():
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("RLS adversarial tests require PostgreSQL.")
    
    engine = create_engine(settings.database_url)
    # Ensure tables exist
    Base.metadata.create_all(engine)
    return engine

@pytest.fixture
def pg_session(pg_engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

def set_workspace(session, workspace_id: str):
    session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id})

def test_rls_insert_and_read_isolation(pg_session):
    """Prove Tenant A cannot see Tenant B's credentials."""
    
    # Setup Tenant A
    set_workspace(pg_session, "tenant_A")
    cred_a = TenantProviderCredential(
        id="cred_A",
        workspace_id="tenant_A",
        provider="openai",
        credential_profile="default",
        auth_type="api_key",
        encrypted_secret="secret_A"
    )
    pg_session.add(cred_a)
    pg_session.commit()
    
    # Read as Tenant B
    set_workspace(pg_session, "tenant_B")
    results = pg_session.query(TenantProviderCredential).filter_by(workspace_id="tenant_A").all()
    assert len(results) == 0, "Tenant B read Tenant A's row!"
    
    # Read as Tenant A
    set_workspace(pg_session, "tenant_A")
    results = pg_session.query(TenantProviderCredential).filter_by(workspace_id="tenant_A").all()
    assert len(results) == 1, "Tenant A cannot read their own row!"

def test_rls_insert_violation(pg_session):
    """Prove Tenant B cannot insert a record claiming to be Tenant A."""
    set_workspace(pg_session, "tenant_B")
    cred = TenantProviderCredential(
        id="cred_B_for_A",
        workspace_id="tenant_A",  # Violates USING/WITH CHECK
        provider="openai",
        credential_profile="default",
        auth_type="api_key"
    )
    pg_session.add(cred)
    
    with pytest.raises(Exception) as excinfo:
        pg_session.commit()
    assert "row level security" in str(excinfo.value).lower(), "RLS did not block mismatched insert!"
    pg_session.rollback()

def test_rls_update_delete_isolation(pg_session):
    """Prove Tenant B cannot update or delete Tenant A's credentials."""
    set_workspace(pg_session, "tenant_A")
    cred = TenantProviderCredential(
        id="cred_A2",
        workspace_id="tenant_A",
        provider="openai",
        credential_profile="update_test",
        auth_type="api_key"
    )
    pg_session.add(cred)
    pg_session.commit()
    
    # Tenant B tries to update
    set_workspace(pg_session, "tenant_B")
    pg_session.execute(
        text("UPDATE tenant_provider_credentials SET provider = 'hacked' WHERE workspace_id = 'tenant_A'")
    )
    pg_session.commit()
    
    # Verify it didn't update
    set_workspace(pg_session, "tenant_A")
    cred_check = pg_session.query(TenantProviderCredential).filter_by(id="cred_A2").first()
    assert cred_check.provider == "openai", "Tenant B successfully updated Tenant A's record!"
    
    # Tenant B tries to delete
    set_workspace(pg_session, "tenant_B")
    pg_session.execute(
        text("DELETE FROM tenant_provider_credentials WHERE workspace_id = 'tenant_A'")
    )
    pg_session.commit()
    
    # Verify it wasn't deleted
    set_workspace(pg_session, "tenant_A")
    cred_check2 = pg_session.query(TenantProviderCredential).filter_by(id="cred_A2").first()
    assert cred_check2 is not None, "Tenant B successfully deleted Tenant A's record!"

def test_rls_missing_workspace(pg_session):
    """Prove missing workspace context results in FAIL CLOSED (no rows)."""
    # Start a fresh transaction without setting app.workspace_id
    results = pg_session.query(TenantProviderCredential).all()
    assert len(results) == 0, "Queries without workspace context returned rows!"

def test_pooled_connection_reuse_contamination(pg_engine):
    """Prove that transaction-scoped RLS prevents pool contamination."""
    
    # Transaction 1: Set context and do work
    with pg_engine.connect() as conn1:
        with conn1.begin():
            conn1.execute(text("SELECT set_config('app.workspace_id', 'tenant_A', true)"))
            # Context is tenant_A
            results = conn1.execute(text("SELECT current_setting('app.workspace_id', true)")).scalar()
            assert results == "tenant_A"
            
    # Connection returned to pool.
    
    # Transaction 2: Same physical connection reused, verify context is wiped
    with pg_engine.connect() as conn2:
        with conn2.begin():
            # In Postgres, if the setting isn't defined, current_setting(..., true) returns NULL or empty string
            results = conn2.execute(text("SELECT current_setting('app.workspace_id', true)")).scalar()
            # Because it was scoped to the transaction (true), it should be empty/null here!
            assert results == "" or results is None, "Connection pool contaminated with previous tenant context!"
