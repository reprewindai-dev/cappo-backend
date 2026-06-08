"""Payment/kill-switch 402 precedence tests (PR #4).

Migration note §7 / EI Plan §Priority rule: a kill-switch or budget-exhaustion
402 takes precedence over the LAW 0 403. The payment gate runs before the
governed pipeline, so a 402 condition short-circuits ahead of any EI work.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun


class TestKillSwitch:
    def test_active_kill_switch_blocks_with_402(self, client: TestClient, db: Session) -> None:
        client.put("/v1/kill-switch/default", json={"active": True, "reason": "incident"})
        resp = client.post("/v1/exec", json={"prompt": "hello"})
        assert resp.status_code == 402
        detail = resp.json()["detail"]
        assert detail["error"] == "PAYMENT_REQUIRED"
        assert detail["reason"] == "kill_switch"

    def test_kill_switch_precedes_pipeline(self, client: TestClient, db: Session) -> None:
        # 402 short-circuits before any run/EI is created.
        client.put("/v1/kill-switch/default", json={"active": True})
        client.post("/v1/exec", json={"prompt": "hello"})
        assert db.query(GovernedRun).count() == 0
        assert db.query(ExecutionIdentity).count() == 0

    def test_deactivated_kill_switch_allows(self, client: TestClient, db: Session) -> None:
        client.put("/v1/kill-switch/default", json={"active": True})
        client.put("/v1/kill-switch/default", json={"active": False})
        resp = client.post("/v1/exec", json={"prompt": "hello"})
        assert resp.status_code == 200


class TestBudget:
    def test_budget_exhaustion_blocks_with_402(self, client: TestClient, db: Session) -> None:
        client.put("/v1/budget/default", json={"balance_cents": 10})
        resp = client.post("/v1/exec", json={"prompt": "hi", "action_cost_cents": 50})
        assert resp.status_code == 402
        assert resp.json()["detail"]["reason"] == "budget_exhausted"

    def test_sufficient_budget_allows(self, client: TestClient, db: Session) -> None:
        client.put("/v1/budget/default", json={"balance_cents": 100})
        # The EI must also be granted authority for the cost (gateway rule 6).
        resp = client.post(
            "/v1/exec",
            json={"prompt": "hi", "action_cost_cents": 50, "budget_approved_cents": 50},
        )
        assert resp.status_code == 200

    def test_no_budget_row_is_unmetered(self, client: TestClient) -> None:
        # No workspace budget row → payment gate does not block. The EI still
        # carries matching authority so gateway rule 6 also passes.
        resp = client.post(
            "/v1/exec",
            json={"prompt": "hi", "action_cost_cents": 9999, "budget_approved_cents": 9999},
        )
        assert resp.status_code == 200
