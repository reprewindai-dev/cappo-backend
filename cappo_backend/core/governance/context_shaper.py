from typing import Dict, Any, List, Tuple
from cappo_backend.core.governance.jurisdiction import PolicyBundle
from cappo_backend.core.governance.execution_policy_engine import ExecutionPolicyEngine
from cappo_backend.core.governance.enforcement_engine import EnforcementEngine
from cappo_backend.core.governance.standard_verifier import StandardVerifier

class ContextShaper:
    """
    The Context Shaper orchestrates the Execution Policy Resolution flow.
    It passes the capability and jurisdiction to the ExecutionPolicyEngine (the Constitutional Court),
    hands the resulting PolicyDecision to the EnforcementEngine, and ensures compliance with
    any required internet standards via the StandardVerifier.
    """
    
    def __init__(self):
        # In a real implementation, this would fetch from /.well-known/capabilities.json dynamically
        self.capability_contracts = {
            "blueprint.generate": {
                "requires": ["tenant", "repository"],
                "allows_pii": ["github_username"],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": ["github_pat"],
                "requires_standards": ["x402", "RFC9989"]
            },
            "financial.transfer": {
                "requires": ["tenant", "amount", "destination"],
                "allows_pii": [],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": [],
                "requires_standards": ["x402"]
            },
            "identity.verify": {
                "requires": ["tenant", "user_id"],
                "allows_pii": ["ssn"],
                "denies_pii": ["email", "phone", "address"],
                "secret_injections": [],
                "requires_standards": ["OAuth2"]
            },
            "github.issue.create": {
                "requires": ["tenant", "issue_title", "issue_body"],
                "allows_pii": ["github_username"],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": ["github_pat"],
                "requires_standards": ["x402"]
            }
        }
        self.resolver = ExecutionPolicyEngine()
        self.enforcer = EnforcementEngine()
        self.standard_verifier = StandardVerifier()
        
    def shape_context(self, capability_id: str, payload: Dict[str, Any], tenant_jwt: str, policy_bundle: PolicyBundle = None) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """
        Orchestrates Execution Policy Resolution, Enforcement, and Standard Verification.
        Returns (shaped_payload, audit_record, enforcement_decision_action)
        """
        contract = self.capability_contracts.get(capability_id)
        if not contract:
            raise ValueError(f"Unknown capability: {capability_id}")
            
        jurisdiction = policy_bundle.jurisdiction if policy_bundle else "Unknown"
        
        # 1. Standard Compliance Verification
        required_standards = contract.get("requires_standards", [])
        standard_results = self.standard_verifier.verify(required_standards, payload)
        blocking_standard_results = [
            result for result in standard_results if result.get("result") != "PASS"
        ]
        
        # 2. Execution Policy Resolution
        decision = self.resolver.resolve(capability_id, jurisdiction)

        # Required standards are part of the capability contract. Missing, failed,
        # unavailable, or unverified standard evidence must therefore deny execution;
        # recording the result in an audit object is not sufficient enforcement.
        if blocking_standard_results:
            blocking_ids = ",".join(result.get("id", "unknown") for result in blocking_standard_results)
            decision.action = "FAIL_CLOSED"
            decision.rule_applied = f"Required-Standard-Verification-Failed:{blocking_ids}"
        
        # 3. Enforcement
        shaped_payload, enforcement_details = self.enforcer.apply(
            decision=decision,
            payload=payload,
            contract=contract,
            policy_bundle=policy_bundle,
            tenant_jwt=tenant_jwt,
            capability_id=capability_id
        )
            
        # 4. Audit Record Generation
        fields_requested = list(payload.keys())
        fields_granted = list(shaped_payload.keys())
        
        audit_record = {
            "capability_id": capability_id,
            "contract_version": "1.0",
            "jurisdiction": decision.jurisdiction,
            "classification": decision.classification,
            "applicable_policies": policy_bundle.applicable_policies if policy_bundle else [],
            "policy_version": decision.policy_version,
            "decision_id": decision.decision_id,
            "enforcement_decision": decision.action,
            "rule_applied": decision.rule_applied,
            "fields_requested": fields_requested,
            "fields_granted": fields_granted,
            "field_decisions": enforcement_details.get("field_decisions", []),
            "secret_injections": enforcement_details.get("secret_injections", []),
            "standards_compliance": standard_results,
            "shaping_reason": f"Applied Execution Policy Resolution for {capability_id} and jurisdiction {jurisdiction}. Decision: {decision.action}"
        }
        
        return shaped_payload, audit_record, decision.action