"""PaymentGate — kill-switch + budget enforcement (HTTP 402).

Migration note §7 / EI Plan §Priority rule: a kill-switch or budget-exhaustion
402 must take precedence over the LAW 0 403. This gate is therefore evaluated
*before* the governed pipeline (and thus before EI enforcement) so that a 402
condition short-circuits ahead of any 403.

It is deliberately a separate, earlier layer: financial/operational gating first,
proof-derived authority (the MCP gateway) second.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.workspace_budget import WorkspaceBudget


class PaymentRequiredError(Exception):
    """Raised when a kill switch is active or the workspace budget is exhausted.

    Maps to HTTP 402, which precedes the LAW 0 403.
    """

    def __init__(self, detail: str, *, reason: str) -> None:
        self.detail = detail
        self.reason = reason  # "kill_switch" | "budget_exhausted"
        super().__init__(detail)


class PaymentGate:
    def __init__(self, db: Session) -> None:
        self._db = db

    def check(self, workspace_id: str, cost_cents: int = 0) -> None:
        """Raise :class:`PaymentRequiredError` if execution must be blocked for
        financial/operational reasons."""
        switch = self._db.get(KillSwitch, workspace_id)
        if switch is not None and switch.active:
            raise PaymentRequiredError(
                f"kill switch active for workspace {workspace_id}"
                + (f": {switch.reason}" if switch.reason else ""),
                reason="kill_switch",
            )

        if cost_cents > 0:
            budget = self._db.get(WorkspaceBudget, workspace_id)
            # No row → unmetered (dev). A row caps spend at balance_cents.
            if budget is not None and budget.balance_cents < cost_cents:
                raise PaymentRequiredError(
                    f"budget exhausted: balance={budget.balance_cents} cents, "
                    f"cost={cost_cents} cents",
                    reason="budget_exhausted",
                )

    # -- kill switch management ------------------------------------------------

    def set_kill_switch(self, workspace_id: str, *, active: bool, reason: str | None = None) -> KillSwitch:
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
            budget = WorkspaceBudget(workspace_id=workspace_id, balance_cents=balance_cents)
            self._db.add(budget)
        else:
            budget.balance_cents = balance_cents
        self._db.flush()
        return budget
