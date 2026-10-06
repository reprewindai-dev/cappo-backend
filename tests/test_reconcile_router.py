"""``POST /api/v1/reconcile/{execution_id}`` is configured, not loopback, and never
asserts ``reconciled_succeeded`` on placeholder evidence.

Authentication for this route is covered in ``test_auth_middleware.py``
(``test_reconcile_is_not_anonymous``).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.api.routers import reconciler_router
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent

_EXEC = "exec-reconcile-1"
_BASE = "http://connector.test:9000"


def _seed_started(db: Session) -> None:
    db.add(
        ConsequenceExecutionEvent(
            event_id="evt-reconcile-0",
            operation_id="op-reconcile",
            intent_hash="intent-hash",
            state="started",
            version=0,
            mount_id="mount-1",
            execution_id=_EXEC,
            principal="ws-alpha",
            action="fs:append",
            resource=None,
        )
    )
    db.commit()


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records the URL and serves one payload."""

    calls: list[str] = []
    payload: dict = {}

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, url: str, timeout: float | None = None) -> httpx.Response:
        _FakeAsyncClient.calls.append(url)
        return httpx.Response(200, json=_FakeAsyncClient.payload, request=httpx.Request("GET", url))


@pytest.fixture
def reconcile_client(client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.payload = {}
    # The router opens its own session; bind it to the test database.
    monkeypatch.setattr(reconciler_router, "SessionLocal", lambda: db)
    monkeypatch.setattr(reconciler_router.httpx, "AsyncClient", _FakeAsyncClient)
    return client


def _states(db: Session) -> list[str]:
    rows = (
        db.query(ConsequenceExecutionEvent)
        .filter_by(execution_id=_EXEC)
        .order_by(ConsequenceExecutionEvent.version.asc())
        .all()
    )
    return [r.state for r in rows]


class TestConnectorConfiguration:
    def test_unconfigured_connector_is_503_and_contacts_nothing(
        self, reconcile_client: TestClient, db: Session
    ) -> None:
        _seed_started(db)
        reconcile_client.app.state.settings.reconcile_connector_base_url = ""

        resp = reconcile_client.post(f"/api/v1/reconcile/{_EXEC}")

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "RECONCILE_CONNECTOR_UNCONFIGURED"
        assert _FakeAsyncClient.calls == []
        assert _states(db) == ["started"]

    def test_connector_url_comes_from_settings(self, reconcile_client: TestClient, db: Session) -> None:
        _seed_started(db)
        reconcile_client.app.state.settings.reconcile_connector_base_url = _BASE + "/"
        _FakeAsyncClient.payload = {"receipt": {"operation_id": "op-target-7"}}

        resp = reconcile_client.post(f"/api/v1/reconcile/{_EXEC}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "reconciled_succeeded"
        assert _FakeAsyncClient.calls == [f"{_BASE}/connectors/sandbox-file-append/status/{_EXEC}"]
        assert "127.0.0.1" not in _FakeAsyncClient.calls[0]
        latest = (
            db.query(ConsequenceExecutionEvent)
            .filter_by(execution_id=_EXEC, state="reconciled_succeeded")
            .one()
        )
        assert latest.proof_subject_hash == "op-target-7"
        assert latest.completion_proof_type == "reconciliation_api_query"


class TestNoPlaceholderEvidence:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"receipt": {}},
            {"receipt": {"operation_id": ""}},
            {"receipt": None},
            {"receipt": "not-a-receipt"},
        ],
    )
    def test_missing_receipt_fails_reconcile_and_writes_nothing(
        self, reconcile_client: TestClient, db: Session, payload: dict
    ) -> None:
        _seed_started(db)
        reconcile_client.app.state.settings.reconcile_connector_base_url = _BASE
        _FakeAsyncClient.payload = payload

        resp = reconcile_client.post(f"/api/v1/reconcile/{_EXEC}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert "receipt.operation_id" in resp.json()["reason"]
        assert _states(db) == ["started"]
        assert db.query(ConsequenceExecutionEvent).filter(
            ConsequenceExecutionEvent.proof_subject_hash == "mock_hash"
        ).count() == 0
