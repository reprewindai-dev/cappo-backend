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
    def __init__(self, db: Session, redis_client: Any | None = None, settings: Any | None = None) -> None:
        self._db = db
        self._settings = settings
        if redis_client is not None:
            self._redis = redis_client
        else:
            import os

            import redis

            from cappo_backend.config import get_settings
            
            s = settings or get_settings()
            url = getattr(s, "redis_url", None) or os.getenv("REDIS_URL")
            if url:
                self._redis = redis.Redis.from_url(
                    url,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                    decode_responses=True
                )
            else:
                self._redis = None

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

        # Enforce Redis-backed limits
        if self._redis is not None:
            import redis
            try:
                # 3. Hard Kill Switch check
                kill_switch_key = f"vnp:kill_switch:workspace:{workspace_id}"
                if self._redis.exists(kill_switch_key):
                    raise PaymentRequiredError(
                        f"Hard kill switch active via Redis for workspace {workspace_id}.",
                        reason="kill_switch",
                    )

                # Get settings for limits
                from cappo_backend.config import get_settings
                s = self._settings or get_settings()
                
                # 1. Workspace Execution Limits (runs and tokens)
                import time
                hour_timestamp = int(time.time() // 3600)
                
                # Check runs limit
                runs_key = f"cappo:limit:workspace:{workspace_id}:runs:{hour_timestamp}"
                current_runs = self._redis.get(runs_key)
                if current_runs is not None and int(current_runs) >= s.max_runs_per_hour:
                    raise PaymentRequiredError(
                        f"Workspace hourly execution limit exceeded ({s.max_runs_per_hour} runs/hour).",
                        reason="rate_limited",
                    )
                
                # Check tokens limit
                tokens_key = f"cappo:limit:workspace:{workspace_id}:tokens:{hour_timestamp}"
                current_tokens = self._redis.get(tokens_key)
                if current_tokens is not None and int(current_tokens) >= s.max_tokens_per_hour:
                    raise PaymentRequiredError(
                        f"Workspace hourly token limit exceeded ({s.max_tokens_per_hour} tokens/hour).",
                        reason="rate_limited",
                    )
                
                # 2. Node Execution Limit (total concurrent runs across the node)
                concurrent_key = "cappo:limit:node:concurrent_runs"
                concurrent = self._redis.incr(concurrent_key)
                if concurrent > s.max_node_concurrent_runs:
                    self._redis.decr(concurrent_key)
                    raise PaymentRequiredError(
                        f"Node concurrent execution limit exceeded ({s.max_node_concurrent_runs} active runs).",
                        reason="node_limit_exceeded",
                    )
                
                # Increment hourly runs count
                self._redis.incr(runs_key)
                self._redis.expire(runs_key, 5400)
                
            except redis.exceptions.RedisError as exc:
                raise PaymentRequiredError(
                    f"Redis connectivity error: {str(exc)}. Failing closed for safety.",
                    reason="redis_unreachable",
                )
        else:
            from cappo_backend.config import get_settings
            s = self._settings or get_settings()
            if s.is_production:
                raise PaymentRequiredError(
                    "Redis connection is required in production for rate limiting.",
                    reason="redis_unreachable",
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
        else:
            # Reset quota if a new day has started
            now = datetime.now(timezone.utc)
            reset_at = quota.reset_at
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            if now >= reset_at:
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

    def decrement_concurrent(self) -> None:
        if self._redis is not None:
            try:
                self._redis.decr("cappo:limit:node:concurrent_runs")
            except Exception:
                pass

    def record_tokens(self, workspace_id: str, tokens: int) -> None:
        if self._redis is not None and tokens > 0:
            try:
                import time
                hour_timestamp = int(time.time() // 3600)
                tokens_key = f"cappo:limit:workspace:{workspace_id}:tokens:{hour_timestamp}"
                self._redis.incrby(tokens_key, tokens)
                self._redis.expire(tokens_key, 5400)
            except Exception:
                pass

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
