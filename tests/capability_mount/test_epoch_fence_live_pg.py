"""Physical verification: monotonic authority epoch commit-fencing against LIVE PostgreSQL.

Executes directly against os.environ['DATABASE_URL'] (cappo-postgres:5432/cappo),
NOT SQLite in-memory.

Invariant H1 & H4:
A consequence authorized under authority_epoch = E1 must be deterministically
refused at begin_consequence if the mount's authority_epoch has advanced to E2
(lease rotation, authority transfer, successor activation).

Result:
- Consequence state is recorded FAILED with error_summary="stale_authority_epoch"
  and completion_proof_type="fence_refusal".
- Physical adapter is never dispatched (invocation_count == 0, delta_C == 0).
- The caller receives Decision.DENY with reason="stale_authority_epoch".
- Physical database rows are permanently committed and read back.
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
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent


class ConfirmedAnchor:
    def anchor(self, event_type: str, **_: object) -> AnchorResult:
        return AnchorResult("confirmed", anchor_id=f"epoch-fence-live-pg-{event_type}-{uuid4().hex}")


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


def test_live_postgres_stale_authority_epoch_refuses_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PHYSICAL TEST: Mount authority_epoch advances in real PostgreSQL 16 before begin_consequence()."""
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

    operation_id = f"epoch-live-pg-{uuid4().hex}"
    advanced_epochs: list[int] = []

    original_evaluate = MountRegistry.evaluate

    def evaluate_then_advance_epoch(self, *args, **kwargs):
        result = original_evaluate(self, *args, **kwargs)
        if result[0] is Decision.ALLOW:
            # Advance authority_epoch directly in LIVE PostgreSQL
            with PgSession() as pg_db:
                row = pg_db.execute(
                    select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
                ).scalar_one()
                row.authority_epoch = 2
                pg_db.commit()
                advanced_epochs.append(row.authority_epoch)
        return result

    monkeypatch.setattr(MountRegistry, "evaluate", evaluate_then_advance_epoch)

    token = mount_body["token"]
    response = client.post(
        f"/v1/capability/mounts/{mount_id}/execute",
        json={
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "record.create",
            "target_ref": LocalRecordAdapter.ref,
            "resource": "epoch-fenced-live-pg",
            "arguments": {"status": "active"},
            "operation_id": operation_id,
        },
    )
    body = response.json()

    # 1. Assert evaluate epoch bump happened
    assert len(advanced_epochs) == 1
    assert advanced_epochs[0] == 2

    # 2. PHYSICAL TRUTH OVER PROJECTION: LocalRecordAdapter was NEVER dispatched (Delta C = 0)
    assert adapter.invocation_count == 0
    assert not (tmp_path / "epoch-fenced-live-pg.json").exists()

    # 3. HTTP consequence boundary returned DENY with reason stale_authority_epoch
    assert body["decision"] == "deny"
    assert body["reason"] == "stale_authority_epoch"

    # 4. PHYSICAL DATABASE READBACK: Query PostgreSQL directly with raw session
    with PgSession() as readback_session:
        events = readback_session.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.operation_id == operation_id)
            .order_by(ConsequenceExecutionEvent.version.asc())
        ).scalars().all()

        assert len(events) == 2, f"Expected exactly 2 events in Postgres, got {len(events)}"

        # Event 0: AUTHORIZED at epoch 1
        assert events[0].state == "authorized"
        assert events[0].authority_epoch == 1
        assert events[0].version == 0

        # Event 1: FAILED at epoch 2 (refused by fence)
        assert events[1].state == "failed"
        assert events[1].authority_epoch == 2
        assert events[1].error_summary == "stale_authority_epoch"
        assert events[1].completion_proof_type == "fence_refusal"
        assert events[1].version == 1
