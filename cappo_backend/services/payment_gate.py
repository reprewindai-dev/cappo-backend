"""PaymentGate — free quota check + kill-switch + budget enforcement (HTTP 402).

Priority order (each layer short-circuits before the next):
  1. Kill switch active         → 402 reason="kill_switch"
  2. Free run quota exhausted   → 402 reason="quota_exhausted"  (pay via x402)
  3. Workspace balance < cost   → 402 reason="budget_exhausted" (top up balance)

The LAW 0 403 (EI enforcement) always comes AFTER a 402. This gate runs first.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from cappo_backend.models.free_run_quota import FREE_RUN_LIMIT, FreeRunQuota
from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.workspace_budget import WorkspaceBudget


class PaymentRequiredError(Exception):
    """Raised when a kill switch is active, free quota exhausted, or budget gone.

    Maps to HTTP 402, which precedes the LAW 0 403.
    """

    def __init__(self, detail: str, *, reason: str) -> None:
        self.detail = detail
        self.reason = reason  # "kill_switch" | "quota_exhausted" | "budget_exhausted"
        super().__init__(detail)


class PaymentGate:
    def __init__(self, db: Session) -> None:
        self._db = db

    def check(self, workspace_id: str, cost_cents: int = 0) -> None:
        """Raise PaymentRequiredError if execution must be blocked.

        Layers checked in order:
          1. Kill switch
          2. Free run quota (1 free run, then x402 required)
          3. Workspace balance (cents-based budget)
        """
        # --- 1. Kill switch ---------------------------------------------------
        switch = self._db.get(KillSwitch, workspace_id)
        if switch is not None and switch.active:
            raise PaymentRequiredError(
                f"kill switch active for workspace {workspace_id}"
                + (f": {switch.reason}" if switch.reason else ""),
                reason="kill_switch",
            )

        # --- 2. Free run quota ------------------------------------------------
        quota = self._db.get(FreeRunQuota, workspace_id)
        if quota is None:
            # First time ever: create row, grant the 1 free run, consume it now
            quota = FreeRunQuota(
                workspace_id=workspace_id,
                runs_used=1,
                quota_limit=FREE_RUN_LIMIT,
            )
            self._db.add(quota)
            self._db.flush()
            return  # Free run granted

        # Reset quota if a new day has started
        now = datetime.now(timezone.utc)
        if now >= quota.reset_at:
            quota.runs_used = 0
            from cappo_backend.models.free_run_quota import _tomorrow
            quota.reset_at = _tomorrow()
            self._db.flush()

        if quota.runs_used >= quota.quota_limit:
            raise PaymentRequiredError(
                f"free run quota exhausted for workspace {workspace_id}. "
                f"Pay via x402 (USDC on Base) to continue.",
                reason="quota_exhausted",
            )

        # Consume the free run
        quota.runs_used += 1
        self._db.flush()

        # --- 3. Workspace balance (optional metered billing) ------------------
        if cost_cents > 0:
            budget = self._db.get(WorkspaceBudget, workspace_id)
            if budget is not None and budget.balance_cents < cost_cents:
                raise PaymentRequiredError(
                    f"budget exhausted: balance={budget.balance_cents} cents, "
                    f"cost={cost_cents} cents",
                    reason="budget_exhausted",
                )

    # -- kill switch management ------------------------------------------------

    def set_kill_switch(
        self, workspace_id: str, *, active: bool, reason: str | None = None
    ) -> KillSwitch:
        switch = self._db.get(KillSwitch, workspace_id)
        if switch is None:
            switch = KillSwitch(workspace_id=workspace_id, active=active, reason=reason)
            self._db.add(switch)
        else:
            switch.active = active
            switch.reason = reason
        self._db.flush()
        return switch

    def set_budget(self, workspace_id: str, balance_cents: int) -> WorkspaceBudget:
        budget = self._db.get(WorkspaceBudget, workspace_id)
        if budget is None:
            budget = WorkspaceBudget(
                workspace_id=workspace_id, balance_cents=balance_cents
            )
            self._db.add(budget)
        else:
            budget.balance_cents = balance_cents
        self._db.flush()
        return budget

    def reset_quota(self, workspace_id: str) -> FreeRunQuota:
        """Admin helper: manually reset a workspace's free run quota."""
        quota = self._db.get(FreeRunQuota, workspace_id)
        if quota is None:
            quota = FreeRunQuota(workspace_id=workspace_id, runs_used=0)
            self._db.add(quota)
        else:
            quota.runs_used = 0
        self._db.flush()
        return quota
