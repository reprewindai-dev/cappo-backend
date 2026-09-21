from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.api.routers.capability_mount_router import anchor_payload
from cappo_backend.capability_mount.effects import (
    GovernedCounterAdapter,
    TargetAdapterRegistry,
)
from cappo_backend.capability_mount.models import (
    EphemeralScopedToken,
    Grants,
    MountPolicy,
    TokenDescriptorScope,
)
from cappo_backend.capability_mount.service import (
    GOVERNED_COUNTER_PACKAGE,
    AnchorResult,
)
from cappo_backend.config import Settings
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.services.mount_pgl import AuditPGLAnchor


def _token() -> EphemeralScopedToken:
    issued_at = datetime.now(timezone.utc)
    return EphemeralScopedToken(
        token_id="token-1",
        mount_id="mount-1",
        execution_id="execution-1",
        package_ref="counter@v1",
        scope=TokenDescriptorScope(workspace="workspace-1", project="project-1"),
        grants=Grants(reads=["counter.read"], writes=["counter.increment"]),
        policy=MountPolicy(),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
        ttl_seconds=300,
        nonce="nonce-1",
    )


def _anchor(db: Session, *, agent_id: str | None = "agent-cappo") -> AuditPGLAnchor:
    return AuditPGLAnchor(
        db,
        Settings(
            pgl_ledger_url="http://gnomledger.test",
            pgl_ledger_agent_id=agent_id,
            pgl_ledger_timeout_ms=100,
        ),
    )


@pytest.mark.parametrize(
    ("event_type", "expected_pgl_type"),
    [
        ("action_decision", "pre_execution_authorization"),
        ("execution", "post_execution_attestation"),
    ],
)
def test_anchor_uses_registered_agent_and_pgl_event_mapping(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    expected_pgl_type: str,
) -> None:
    posted: dict[str, object] = {}

    def mock_post(url: str, **kwargs: object) -> httpx.Response:
        posted.update(kwargs.get("json", {}))
        return httpx.Response(
            201,
            json={"persisted": True, "event_hash": "external-abc"},
        )

    monkeypatch.setattr(httpx, "post", mock_post)

    result = _anchor(db).anchor(
        event_type,
        action="counter.increment",
        decision="allow",
        reason="test",
        mount=None,
        token=_token(),
    )

    assert result.status == "confirmed"
    assert result.external_ref == "external-abc"
    assert posted["agent_id"] == "agent-cappo"
    assert posted["event_type"] == expected_pgl_type
    assert posted["idempotency_key"] == result.anchor_id
    assert posted["details"]["event_type"] == event_type
    assert posted["details"]["execution_id"] == "execution-1"
    assert anchor_payload(result, Settings(pgl_ledger_agent_id="agent-cappo"))["pgl_agent_id"] == "agent-cappo"


def test_pgl_agent_id_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGL_LEDGER_AGENT_ID", "agent-from-env")

    assert Settings(_env_file=None).pgl_ledger_agent_id == "agent-from-env"


def test_anchor_requires_confirmed_persisted_event_hash(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(200),
    )

    result = _anchor(db).anchor(
        "mount",
        action="mount",
        decision="allow",
        reason="test",
        mount=None,
        token=None,
    )

    assert result.status == "pending_reconciliation"
    assert result.detail == "external PGL response lacked event_hash"
    assert anchor_payload(result, Settings(pgl_ledger_agent_id="agent-cappo"))["pgl_agent_id"] is None


def test_missing_agent_id_does_not_post(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_post(*args: object, **kwargs: object) -> httpx.Response:
        raise AssertionError("PGL must not be called without a configured agent")

    monkeypatch.setattr(httpx, "post", unexpected_post)

    result = _anchor(db, agent_id=None).anchor(
        "mount",
        action="mount",
        decision="allow",
        reason="test",
        mount=None,
        token=None,
    )

    assert result.status == "pending_reconciliation"
    assert result.detail == "PGL_LEDGER_AGENT_ID is not configured"
    assert anchor_payload(result, Settings(pgl_ledger_agent_id=None))["pgl_agent_id"] is None


def test_not_applicable_anchor_does_not_expose_agent_id() -> None:
    assert anchor_payload(
        AnchorResult("not_applicable"),
        Settings(pgl_ledger_agent_id="agent-cappo"),
    )["pgl_agent_id"] is None


def test_404_remains_pending_reconciliation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: httpx.Response(404),
    )

    result = _anchor(db).anchor(
        "terminate",
        action="execution",
        decision="allow",
        reason="test",
        mount=None,
        token=None,
    )

    assert result.status == "pending_reconciliation"
    assert result.detail == "external PGL append unconfirmed"


def test_execute_persists_and_exposes_external_event_hash(
    client: TestClient,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[dict[str, object]] = []

    def mock_post(url: str, **kwargs: object) -> httpx.Response:
        posted.append(kwargs.get("json", {}))
        return httpx.Response(
            201,
            json={"persisted": True, "event_hash": "abc"},
        )

    monkeypatch.setattr(httpx, "post", mock_post)
    settings = Settings(
        pgl_ledger_url="http://gnomledger.test",
        pgl_ledger_agent_id="agent-cappo",
        pgl_ledger_timeout_ms=100,
    )
    client.app.state.settings = settings
    client.app.state.mount_registry.anchor = AuditPGLAnchor(db, settings)
    client.app.state.mount_registry.register_package(GOVERNED_COUNTER_PACKAGE)
    adapter = GovernedCounterAdapter(tmp_path)
    client.app.state.mount_registry.target_adapters = TargetAdapterRegistry()
    client.app.state.mount_registry.target_adapters.register(
        GovernedCounterAdapter.ref,
        adapter,
    )
    client.headers["X-Workspace-ID"] = "workspace-1"

    mounted = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": GOVERNED_COUNTER_PACKAGE.id,
            "execution_scope": {
                "workspace": "workspace-1",
                "project": "project-1",
            },
            "requested_action_scope": {
                "reads": ["counter.read"],
                "writes": ["counter.increment"],
                "blocked": ["counter.reset"],
            },
            "ttl_seconds": 300,
        },
    )
    assert mounted.status_code == 200
    mount = mounted.json()
    assert mount["anchoring"]["pgl_event_hash"] == "abc"
    assert mount["anchoring"]["pgl_agent_id"] == "agent-cappo"

    executed = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json={
            "token_id": mount["token"]["token_id"],
            "nonce": mount["token"]["nonce"],
            "action": "counter.increment",
            "target_ref": GovernedCounterAdapter.ref,
            "resource": "demo-1",
            "arguments": {},
            "operation_id": "pgl-hash-execute",
        },
    )
    assert executed.status_code == 200
    body = executed.json()
    receipt_id = body["consequence"]["receipt_id"]
    receipt = db.get(CapabilityActionReceipt, receipt_id)

    assert receipt is not None
    assert receipt.pgl_event_hash == "abc"
    assert body["anchoring"]["pgl_event_hash"] == "abc"
    assert body["anchoring"]["pgl_agent_id"] == "agent-cappo"
    assert any(
        event["details"]["event_type"] == "action_decision"
        for event in posted
    )
