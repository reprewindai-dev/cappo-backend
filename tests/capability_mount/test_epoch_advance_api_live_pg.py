"""Physical verification: monotonic authority epoch advance and caller generation gating against LIVE PostgreSQL.

Executes directly against os.environ['DATABASE_URL'] (cappo-postgres:5432/cappo),
with NO monkeypatching and NO synthetic raw DB overrides.

Invariant:
1. An active capability mount is initialized with authority_epoch = 1.
2. An authority epoch rotation (advance-epoch) moves the mount's authority_epoch to 2 in PostgreSQL.
3. A caller presenting a stale authority_epoch (epoch 1) is deterministically refused with 'stale_authority_epoch'
   before any target invocation occurs (Delta C = 0).
4. A caller presenting the current authority_epoch (epoch 2) is permitted, executes on the target (Delta C = 1),
   and terminates normally.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

from cappo_backend.capability_mount.effects import (
    LocalRecordAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
)
from cappo_backend.capability_mount.service import AnchorResult, MountRegistry
from cappo_backend.config import Settings, get_settings as _gs
from cappo_backend.db.session import get_session, get_unscoped_session
from cappo_backend.main import create_app
from cappo_backend.models.capability_mount import CapabilityMount


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"epoch-advance-live-pg-{event_type}-{uuid4().hex}")


def records_package(package_id: str = "records@v1") -> CapabilityPackage:
    return CapabilityPackage(
        id=package_id,
        family="activation",
        title="Activation Records",
        purpose="Manage activation records",
        reads=["record.read"],
        writes=["record.create"],
        outputs=["record"],
        policy_defaults={"mode": "record"},
    )


class InjectWorkspaceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        workspace = request.headers.get("X-Workspace-ID") or "w1"
        request.scope["auth_workspace"] = workspace
        request.scope["auth_principal"] = "test:principal"
        request.scope["caller_spiffe_id"] = "spiffe://example.org/workload/cappo-backend"
        return await call_next(request)


def test_live_postgres_epoch_advance_and_caller_gating(tmp_path: Path) -> None:
    """PHYSICAL TEST: Advance mount authority_epoch via API and verify caller generation gating in real PostgreSQL 16."""
    db_url = os.environ.get("DATABASE_URL")
    assert db_url and "postgres" in db_url, "Must run against live PostgreSQL"

    pg_engine = create_engine(db_url)
    PgSession = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)

    settings = Settings(
        database_url=db_url,
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

    test_app = create_app(settings)

    def _override_session() -> Iterator[Session]:
        with PgSession() as sess:
            yield sess

    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[get_unscoped_session] = _override_session
    test_app.dependency_overrides[_gs] = lambda: settings
    test_app.add_middleware(InjectWorkspaceMiddleware)

    adapter = LocalRecordAdapter(tmp_path)
    registry = test_app.state.mount_registry
    registry.register_package(records_package())
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)

    client = TestClient(test_app)
    client.headers.update({
        "X-API-Key": "test-key",
        "X-Workspace-ID": "w1",
        "X-Wallet-Address": "test-wallet",
    })

    # Step 1: Mount a capability at default authority_epoch = 1
    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": "records@v1",
            "execution_scope": {"workspace": "w1", "project": "p1"},
            "requested_action_scope": {"reads": [], "writes": ["record.create"]},
            "ttl_seconds": 300,
        },
    )
    assert mounted.status_code == 200, f"Mount failed: {mounted.text}"
    mount_body = mounted.json()
    assert mount_body["decision"] == "allow"
    mount_id = mount_body["mount"]["id"]

    # Verify initial epoch in PostgreSQL
    with PgSession() as sess:
        mount_row = sess.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        assert mount_row.authority_epoch == 1
        assert not mount_row.terminated

    # Step 2: Advance authority epoch via explicit REST endpoint (e.g. lease rotation / successor activation)
    adv_res = client.post(f"/v1/capability/mounts/{mount_id}/advance-epoch")
    assert adv_res.status_code == 200, f"Advance epoch failed: {adv_res.text}"
    adv_body = adv_res.json()
    assert adv_body["authority_epoch"] == 2
    assert adv_body["mount_id"] == mount_id

    # Verify physical PostgreSQL row shows authority_epoch = 2 and still unterminated
    with PgSession() as sess:
        mount_row = sess.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        assert mount_row.authority_epoch == 2
        assert not mount_row.terminated

    # Step 3: Adversarial attempt: Caller attempts execution with stale authority_epoch = 1
    token = mount_body["token"]
    stale_op_id = f"stale-epoch-op-{uuid4().hex}"
    stale_resp = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "record.create",
            "target_ref": LocalRecordAdapter.ref,
            "resource": "stale-record",
            "arguments": {"status": "unauthorized"},
            "operation_id": stale_op_id,
            "authority_epoch": 1,  # STALE generation
        },
    )
    assert stale_resp.status_code == 200
    stale_body = stale_resp.json()
    assert stale_body["decision"] == "deny"
    assert stale_body["reason"] == "stale_authority_epoch"
    assert not stale_body["consequence"]["target_invoked"]
    assert adapter.invocation_count == 0  # Delta C = 0
    assert not (tmp_path / "stale-record.json").exists()

    # Step 4: Legitimate attempt: Caller presents current authority_epoch = 2
    legit_op_id = f"legit-epoch-op-{uuid4().hex}"
    legit_resp = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "record.create",
            "target_ref": LocalRecordAdapter.ref,
            "resource": "legit-record",
            "arguments": {"status": "authorized"},
            "operation_id": legit_op_id,
            "authority_epoch": 2,  # CURRENT generation
        },
    )
    assert legit_resp.status_code == 200
    legit_body = legit_resp.json()
    assert legit_body["decision"] == "allow"
    assert legit_body["reason"] == "allowed"
    assert legit_body["consequence"]["target_invoked"]
    assert adapter.invocation_count == 1  # Delta C = 1 physically verified!
    assert (tmp_path / "legit-record.json").exists()

    # Verify physical PostgreSQL final state
    with PgSession() as sess:
        mount_row = sess.execute(
            select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        ).scalar_one()
        assert mount_row.terminated
        assert mount_row.authority_epoch == 3  # Bumped upon normal termination
