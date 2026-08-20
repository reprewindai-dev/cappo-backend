"""Payment/kill-switch 402 precedence tests (PR #4).

Migration note §7 / EI Plan §Priority rule: a kill-switch or budget-exhaustion
402 takes precedence over the LAW 0 403. The payment gate runs before the
governed pipeline, so a 402 condition short-circuits ahead of any EI work.

P0-1 note: conftest `client` fixture now injects auth_workspace="test-workspace"
via InjectWorkspaceMiddleware. Kill-switch and budget endpoints are keyed by
workspace_id; tests now use "test-workspace" to match the injected workspace.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun

# Workspace used by conftest InjectWorkspaceMiddleware
_WS = "test-workspace"


class TestKillSwitch:
    def test_active_kill_switch_blocks_with_402(self, client: TestClient, db: Session) -> None:
        client.put(f"/v1/kill-switch/{_WS}", json={"active": True, "reason": "incident"})
        resp = client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"})
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert detail["error"] == "PAYMENT_REQUIRED"
        assert detail["reason"] == "kill_switch"

    def test_kill_switch_precedes_pipeline(self, client: TestClient, db: Session) -> None:
        # 402 short-circuits before any run/EI is created.
        client.put(f"/v1/kill-switch/{_WS}", json={"active": True})
        client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id"})
        assert db.query(GovernedRun).count() == 0
        assert db.query(ExecutionIdentity).count() == 0

    def test_deactivated_kill_switch_allows(self, client: TestClient, db: Session) -> None:
        client.put(f"/v1/kill-switch/{_WS}", json={"active": True})
        client.put(f"/v1/kill-switch/{_WS}", json={"active": False})
        resp = client.post("/v1/exec", json={"prompt": "hello", "pgl_id": "test-user-id", "directive": "ALLOW"})
        assert resp.status_code == 200


class TestBudget:
    def test_budget_exhaustion_blocks_with_402(self, client: TestClient, db: Session) -> None:
        client.put(f"/v1/budget/{_WS}", json={"balance_cents": 10})
        resp = client.post("/v1/exec", json={"prompt": "hi", "pgl_id": "test-user-id", "action_cost_cents": 50})
        assert resp.status_code == 402
        assert resp.json()["detail"]["reason"] == "budget_exhausted"

    def test_sufficient_budget_allows(self, client: TestClient, db: Session) -> None:
        client.put(f"/v1/budget/{_WS}", json={"balance_cents": 100})
        # The EI must also be granted authority for the cost (gateway rule 6).
        resp = client.post(
            "/v1/exec",
            json={"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW", "action_cost_cents": 50, "budget_approved_cents": 50},
        )
        assert resp.status_code == 200

    def test_no_budget_row_is_unmetered(self, client: TestClient) -> None:
        # No workspace budget row → payment gate does not block. The EI still
        # carries matching authority so gateway rule 6 also passes.
        resp = client.post(
            "/v1/exec",
            json={"prompt": "hi", "pgl_id": "test-user-id", "directive": "ALLOW", "action_cost_cents": 9999, "budget_approved_cents": 9999},
        )
        assert resp.status_code == 200
