from typing import Dict, Any, List, Tuple
import json
from cappo_backend.core.governance.jurisdiction import PolicyBundle

class ContextShaper:
    """
    The Context Shaper is a first-class primitive in the Trust Spine.
    It sits between the reasoning engine and execution as the Execution Policy Resolution engine.
    It reads capability contracts, evaluates the Policy Matrix (Capability x Jurisdiction),
    and makes decisions: PROCEED, STRIP, FAIL_CLOSED, or ESCALATE.
    """
    
    def __init__(self):
        # In a real implementation, this would fetch from /.well-known/capabilities.json dynamically
        self.capability_contracts = {
            "blueprint.generate": {
                "requires": ["tenant", "repository"],
                "allows_pii": ["github_username"],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": ["github_pat"]
            },
            "financial.transfer": {
                "requires": ["tenant", "amount", "destination"],
                "allows_pii": [],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": []
            },
            "identity.verify": {
                "requires": ["tenant", "user_id"],
                "allows_pii": ["ssn"],
                "denies_pii": ["email", "phone", "address"],
                "secret_injections": []
            }
        }
        
        # Policy Matrix: (Capability, Jurisdiction) -> Enforcement Decision
        self.policy_matrix = {
            ("blueprint.generate", "Canada"): "STRIP",
            ("financial.transfer", "Canada"): "FAIL_CLOSED",
            ("healthcare.access", "Canada"): "FAIL_CLOSED",
            ("public.search", "EU"): "STRIP",
            ("identity.verify", "*"): "FAIL_CLOSED"
        }
        
    def _resolve_decision(self, capability_id: str, jurisdiction: str) -> str:
        """
        Resolves the enforcement decision from the Policy Matrix.
        Defaults to FAIL_CLOSED for safety if undefined.
        """
        # Specific match
        decision = self.policy_matrix.get((capability_id, jurisdiction))
        if decision:
            return decision
            
        # Wildcard jurisdiction match
        decision = self.policy_matrix.get((capability_id, "*"))
        if decision:
            return decision
            
        # Default safety fallback
        return "FAIL_CLOSED"
        
    def shape_context(self, capability_id: str, payload: Dict[str, Any], tenant_jwt: str, policy_bundle: PolicyBundle = None) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        """
        Shapes the context for a capability request.
        Returns (shaped_payload, audit_record, enforcement_decision)
        """
        contract = self.capability_contracts.get(capability_id)
        if not contract:
            raise ValueError(f"Unknown capability: {capability_id}")
            
        jurisdiction = policy_bundle.jurisdiction if policy_bundle else "Unknown"
        enforcement_decision = self._resolve_decision(capability_id, jurisdiction)
            
        fields_requested = list(payload.keys())
        fields_granted = []
        field_decisions = []
        
        shaped_payload = {}
        
        # Merge capability-level denies with jurisdiction-level strict denies
        contract_denies = set(contract.get("denies_pii", []))
        global_denies = set(policy_bundle.global_denies_pii if policy_bundle else [])
        effective_denies = contract_denies.union(global_denies)
        
        # Track if we found any violations that trigger the enforcement decision
        violation_found = False
        
        # 1. PII Minimization & Policy Evaluation
        for key, value in payload.items():
            if key in effective_denies:
                violation_found = True
                policy_reason = "Global Jurisdiction Policy" if key in global_denies else "Capability Contract prohibits exposure"
                
                # Determine classification (naive mock for now, in reality this would use an ML classifier or static map)
                classification = "PII"
                
                field_decisions.append({
                    "field": key,
                    "classification": classification,
                    "jurisdiction": jurisdiction,
                    "policy": ", ".join(policy_bundle.applicable_policies) if policy_bundle else policy_reason,
                    "decision": enforcement_decision,
                    "reason": policy_reason,
                    "actor": "ContextShaper"
                })
            else:
                shaped_payload[key] = value
                fields_granted.append(key)
                
        # 2. Secret Injection (Mocked - would call Lockerphycer with tenant_jwt)
        injected_secrets = []
        for secret in contract.get("secret_injections", []):
            # The reasoning brain never sees this value
            injected_key = f"_injected_{secret}"
            shaped_payload[injected_key] = f"vault_secret_{secret}_{tenant_jwt[:10]}"
            injected_secrets.append(secret)
            
        # 3. Audit Record Generation
        audit_record = {
            "capability_id": capability_id,
            "contract_version": "1.0",
            "jurisdiction": jurisdiction,
            "applicable_policies": policy_bundle.applicable_policies if policy_bundle else [],
            "policy_version": policy_bundle.policy_version if policy_bundle else "0.0",
            "enforcement_decision": enforcement_decision,
            "fields_requested": fields_requested,
            "fields_granted": fields_granted if (not violation_found or enforcement_decision == "STRIP") else [],
            "field_decisions": field_decisions,
            "secret_injections": injected_secrets if (not violation_found or enforcement_decision == "STRIP") else [],
            "shaping_reason": f"Applied Execution Policy Resolution for {capability_id} and jurisdiction {jurisdiction}. Decision: {enforcement_decision}"
        }
        
        # If a violation was found and the decision is NOT STRIP, the payload is completely cleared
        if violation_found and enforcement_decision != "STRIP":
            shaped_payload = {}
        
        return shaped_payload, audit_record, enforcement_decision
