"""Workspace-keyed admin controls are bound to the caller's own workspace.

``PUT /v1/kill-switch/{workspace_id}`` and ``PUT /v1/budget/{workspace_id}``
take the workspace from the path. The path must name the caller's
authenticated workspace (``auth_workspace`` in the ASGI scope); any other
workspace is refused with 403 WORKSPACE_SCOPE_MISMATCH.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.workspace_budget import WorkspaceBudget

_OWN = "ws-alpha"
_OTHER = "ws-beta"
_HEADERS = {"X-Workspace-ID": _OWN}


class TestKillSwitchScope:
    def test_other_workspace_is_refused(self, client: TestClient, db: Session) -> None:
        resp = client.put(f"/v1/kill-switch/{_OTHER}", json={"active": True}, headers=_HEADERS)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "WORKSPACE_SCOPE_MISMATCH"
        assert db.query(KillSwitch).filter_by(workspace_id=_OTHER).count() == 0

    def test_own_workspace_is_allowed(self, client: TestClient) -> None:
        resp = client.put(f"/v1/kill-switch/{_OWN}", json={"active": True}, headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"workspace_id": _OWN, "active": True, "reason": None}


class TestBudgetScope:
    def test_other_workspace_is_refused(self, client: TestClient, db: Session) -> None:
        resp = client.put(f"/v1/budget/{_OTHER}", json={"balance_cents": 10}, headers=_HEADERS)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "WORKSPACE_SCOPE_MISMATCH"
        assert db.query(WorkspaceBudget).filter_by(workspace_id=_OTHER).count() == 0

    def test_own_workspace_is_allowed(self, client: TestClient) -> None:
        resp = client.put(f"/v1/budget/{_OWN}", json={"balance_cents": 10}, headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"workspace_id": _OWN, "balance_cents": 10}
