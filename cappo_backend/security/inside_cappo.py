"""Inside CAPPO Module — Dynamic Runtime Authorization.

This module evaluates policy *during* execution, allowing for dynamic
re-authorization, kill switch enforcement, and mid-flight aborts.
"""

from typing import Any, Dict

from cappo_backend.db.session import SessionLocal
from cappo_backend.services.payment_gate import PaymentGate


class InsideCAPPO:
    """Enforces runtime security policies dynamically mid-flight."""
    
    @staticmethod
    def evaluate_kill_switch(workspace_id: str) -> None:
        """
        Check if the workspace kill switch has been flipped during execution.
        Raises PaymentRequiredError (HTTP 402) if disabled.
        """
        db = SessionLocal()
        try:
            # We reuse the PaymentGate to check workspace budget and kill_switch statuses.
            PaymentGate(db).check(workspace_id, cost_cents=0)
        finally:
            db.close()

    @staticmethod
    def check_midflight(eat: Dict[str, Any], current_action: str) -> None:
        """
        Verify that an execution context is still valid mid-flight.
        
        Args:
            eat: The original Execution Authorization Token
            current_action: The action the agent is currently trying to perform
        
        Raises:
            Exception if the EAT does not authorize the current sub-action.
        """
        auth = eat.get("authorization", {})
        scope = auth.get("scope", {})
        tools = scope.get("tools", [])
        
        if tools and current_action not in tools:
            raise Exception(
                f"Mid-flight authorization failed: {current_action!r} not in approved scope {tools!r}"
            )
