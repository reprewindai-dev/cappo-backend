from typing import Dict, Any, List, Tuple
import json
from cappo_backend.core.governance.jurisdiction import PolicyBundle

class ContextShaper:
    """
    The Context Shaper is a first-class primitive in the Trust Spine.
    It sits between the reasoning engine and execution.
    It reads capability contracts, strips denied PII, and injects required secrets.
    """
    
    def __init__(self):
        # In a real implementation, this would fetch from /.well-known/capabilities.json dynamically
        self.capability_contracts = {
            "blueprint.generate": {
                "requires": ["tenant", "repository"],
                "allows_pii": ["github_username"],
                "denies_pii": ["email", "phone", "address", "ssn"],
                "secret_injections": ["github_pat"]
            }
        }
        
    def shape_context(self, capability_id: str, payload: Dict[str, Any], tenant_jwt: str, policy_bundle: PolicyBundle = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Shapes the context for a capability request.
        Returns (shaped_payload, audit_record)
        """
        contract = self.capability_contracts.get(capability_id)
        if not contract:
            raise ValueError(f"Unknown capability: {capability_id}")
            
        fields_requested = list(payload.keys())
        fields_granted = []
        field_decisions = []
        
        shaped_payload = {}
        
        # Merge capability-level denies with jurisdiction-level strict denies
        contract_denies = set(contract.get("denies_pii", []))
        global_denies = set(policy_bundle.global_denies_pii if policy_bundle else [])
        effective_denies = contract_denies.union(global_denies)
        
        # 1. PII Minimization
        for key, value in payload.items():
            if key in effective_denies:
                policy_reason = "Global Jurisdiction Policy" if key in global_denies else "Capability Contract"
                field_decisions.append({
                    "field": key,
                    "action": "Denied",
                    "policy": ", ".join(policy_bundle.applicable_policies) if policy_bundle else policy_reason,
                    "jurisdiction": policy_bundle.jurisdiction if policy_bundle else "Unknown",
                    "contract_version": "1.0"
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
            "jurisdiction": policy_bundle.jurisdiction if policy_bundle else "Unknown",
            "applicable_policies": policy_bundle.applicable_policies if policy_bundle else [],
            "policy_version": policy_bundle.policy_version if policy_bundle else "0.0",
            "fields_requested": fields_requested,
            "fields_granted": fields_granted,
            "field_decisions": field_decisions,
            "secret_injections": injected_secrets,
            "shaping_reason": f"Applied governance contract for {capability_id} and jurisdiction {policy_bundle.jurisdiction if policy_bundle else 'Unknown'}"
        }
        
        return shaped_payload, audit_record
