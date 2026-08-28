import uuid

from pydantic import BaseModel

from cappo_backend.core.governance.policy_objects import CAPABILITY_POLICIES


class PolicyDecision(BaseModel):
    decision_id: str
    action: str
    jurisdiction: str
    classification: list[str]
    policy_version: str
    rule_applied: str

class ExecutionPolicyEngine:
    """
    The Constitutional Court of the system.
    Evaluates identity, capability, and jurisdiction against the defined policy objects.
    """
    
    def __init__(self):
        self.policies = CAPABILITY_POLICIES

    def resolve(self, capability_id: str, jurisdiction: str) -> PolicyDecision:
        """
        Resolves the policy decision for a given capability and jurisdiction.
        """
        best_match = None
        
        for policy in self.policies:
            if policy["capability"] == capability_id:
                if policy["jurisdiction"] == jurisdiction:
                    best_match = policy
                    break
                elif policy["jurisdiction"] == "*" or policy["jurisdiction"] == "Global":
                    best_match = policy
                    
        decision_id = f"pd_{uuid.uuid4().hex[:12]}"
                    
        if best_match:
            return PolicyDecision(
                decision_id=decision_id,
                action=best_match["enforcement"]["action"],
                jurisdiction=best_match["jurisdiction"],
                classification=best_match["classification"],
                policy_version=best_match["version"],
                rule_applied=f"Policy-Match-{capability_id}-{best_match['jurisdiction']}"
            )
            
        # Default safety fallback
        return PolicyDecision(
            decision_id=decision_id,
            action="FAIL_CLOSED",
            jurisdiction=jurisdiction,
            classification=["Unknown"],
            policy_version="0.0",
            rule_applied="Default-Deny"
        )
