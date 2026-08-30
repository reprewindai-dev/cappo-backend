from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cappo_backend.config import get_settings
from cappo_backend.models.tenant_provider_credential import TenantProviderCredential

# These tests REQUIRE PostgreSQL and a dedicated non-owner, non-superuser,
# NOBYPASSRLS application role. The bootstrap/migration role is intentionally
# not accepted as proof because PostgreSQL superusers bypass row security.


@pytest.fixture(scope="session")
def pg_admin_engine():
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("RLS adversarial tests require PostgreSQL.")
    return create_engine(settings.database_url, pool_pre_ping=True)


@pytest.fixture(scope="session")
def pg_engine(pg_admin_engine):
    application_url = os.getenv("RLS_DATABASE_URL", "").strip()
    if not application_url.startswith("postgresql"):
        pytest.skip("RLS adversarial tests require RLS_DATABASE_URL for a non-bypass role.")

    engine = create_engine(application_url, pool_pre_ping=True)
    with engine.connect() as conn:
        identity = conn.execute(
            text(
                """
                SELECT current_user, r.rolsuper, r.rolbypassrls
                FROM pg_roles AS r
                WHERE r.rolname = current_user
                """
            )
        ).one()
        assert identity.rolsuper is False, "RLS proof role must not be a PostgreSQL superuser"
        assert identity.rolbypassrls is False, "RLS proof role must be NOBYPASSRLS"

        owner = conn.execute(
            text(
                """
                SELECT pg_get_userbyid(c.relowner)
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'tenant_provider_credentials'
                """
            )
        ).scalar_one()
        assert owner != identity.current_user, "RLS proof role must not own the protected table"

        rls_state = conn.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = 'tenant_provider_credentials'
                """
            )
        ).one()
        assert rls_state.relrowsecurity is True, "RLS must be enabled on provider credentials"
        assert rls_state.relforcerowsecurity is True, "Provider credentials must FORCE RLS"

    return engine


@pytest.fixture
def pg_session(pg_engine, pg_admin_engine):
    # Test cleanup is deliberately performed with the bootstrap/admin connection,
    # never by granting cross-tenant TRUNCATE authority to the application role.
    with pg_admin_engine.begin() as admin:
        admin.execute(text("TRUNCATE TABLE tenant_provider_credentials CASCADE"))

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def set_workspace(session, workspace_id: str):
    session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"),
        {"ws": workspace_id},
    )


def test_rls_insert_and_read_isolation(pg_session):
    """Prove Tenant B cannot read Tenant A's provider credential."""
    set_workspace(pg_session, "tenant_A")
    cred_a = TenantProviderCredential(
        id="cred_A",
        workspace_id="tenant_A",
        provider="openai",
        credential_profile="default",
        auth_type="api_key",
        encrypted_secret="secret_A",
    )
    pg_session.add(cred_a)
    pg_session.commit()

    set_workspace(pg_session, "tenant_B")
    results = (
        pg_session.query(TenantProviderCredential)
        .filter_by(workspace_id="tenant_A")
        .all()
    )
    assert len(results) == 0, "Tenant B read Tenant A's row!"

    set_workspace(pg_session, "tenant_A")
    results = (
        pg_session.query(TenantProviderCredential)
        .filter_by(workspace_id="tenant_A")
        .all()
    )
    assert len(results) == 1, "Tenant A cannot read their own row!"


def test_rls_insert_violation(pg_session):
    """Prove Tenant B cannot insert a record claiming to be Tenant A."""
    set_workspace(pg_session, "tenant_B")
    cred = TenantProviderCredential(
        id="cred_B_for_A",
        workspace_id="tenant_A",
        provider="openai",
        credential_profile="default",
        auth_type="api_key",
    )
    pg_session.add(cred)

    with pytest.raises(Exception) as excinfo:
        pg_session.commit()
    err_msg = str(excinfo.value).lower()
    assert (
        "row level security" in err_msg or "row-level security" in err_msg
    ), "RLS did not block mismatched insert!"
    pg_session.rollback()


def test_rls_update_delete_isolation(pg_session):
    """Prove Tenant B cannot update or delete Tenant A's credentials."""
    set_workspace(pg_session, "tenant_A")
    cred = TenantProviderCredential(
        id="cred_A2",
        workspace_id="tenant_A",
        provider="openai",
        credential_profile="update_test",
        auth_type="api_key",
    )
    pg_session.add(cred)
    pg_session.commit()

    set_workspace(pg_session, "tenant_B")
    pg_session.execute(
        text(
            "UPDATE tenant_provider_credentials "
            "SET provider = 'hacked' WHERE workspace_id = 'tenant_A'"
        )
    )
    pg_session.commit()

    set_workspace(pg_session, "tenant_A")
    cred_check = (
        pg_session.query(TenantProviderCredential).filter_by(id="cred_A2").first()
    )
    assert cred_check is not None
    assert cred_check.provider == "openai", "Tenant B successfully updated Tenant A's record!"

    set_workspace(pg_session, "tenant_B")
    pg_session.execute(
        text("DELETE FROM tenant_provider_credentials WHERE workspace_id = 'tenant_A'")
    )
    pg_session.commit()

    set_workspace(pg_session, "tenant_A")
    cred_check2 = (
        pg_session.query(TenantProviderCredential).filter_by(id="cred_A2").first()
    )
    assert cred_check2 is not None, "Tenant B successfully deleted Tenant A's record!"


def test_rls_missing_workspace(pg_session):
    """Prove missing workspace context fails closed with no visible rows."""
    results = pg_session.query(TenantProviderCredential).all()
    assert len(results) == 0, "Queries without workspace context returned rows!"


def test_pooled_connection_reuse_contamination(pg_engine):
    """Prove transaction-scoped RLS context cannot contaminate pool reuse."""
    with pg_engine.connect() as conn1:
        with conn1.begin():
            conn1.execute(text("SELECT set_config('app.workspace_id', 'tenant_A', true)"))
            results = conn1.execute(
                text("SELECT current_setting('app.workspace_id', true)")
            ).scalar()
            assert results == "tenant_A"

    with pg_engine.connect() as conn2:
        with conn2.begin():
            results = conn2.execute(
                text("SELECT current_setting('app.workspace_id', true)")
            ).scalar()
            assert results == "" or results is None, (
                "Connection pool contaminated with previous tenant context!"
            )
