import datetime
from typing import Any, Dict, Tuple

from cappo_backend.core.governance.execution_policy_engine import PolicyDecision
from cappo_backend.core.governance.jurisdiction import PolicyBundle


class EnforcementEngine:
    """
    Applies the PolicyDecision to the raw payload.
    """
    
    def apply(self, decision: PolicyDecision, payload: Dict[str, Any], contract: Dict[str, Any], policy_bundle: PolicyBundle, tenant_jwt: str, capability_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Returns (shaped_payload, enforcement_audit_details)
        """
        shaped_payload = {}
        field_decisions = []
        
        # Merge capability-level denies with jurisdiction-level strict denies
        contract_denies = set(contract.get("denies_pii", []))
        global_denies = set(policy_bundle.global_denies_pii if policy_bundle else [])
        effective_denies = contract_denies.union(global_denies)
        
        if decision.action == "FAIL_CLOSED" or decision.action == "DENY":
            # Strip everything if we are failing closed
            for key in payload.keys():
                field_decisions.append({
                    "field": key,
                    "classification": "Unknown",
                    "requested_by": capability_id,
                    "policy": ", ".join(policy_bundle.applicable_policies) if policy_bundle else "Global Policy",
                    "rule": decision.rule_applied,
                    "decision_id": decision.decision_id,
                    "decision": decision.action,
                    "reason": "Request blocked by Enforcement Engine",
                    "resolver_version": decision.policy_version,
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                })
            return {}, {"field_decisions": field_decisions, "decision_id": decision.decision_id}
            
        import re
        
        PII_REGEXES = {
            "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
            "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
            "phone": re.compile(r'\b(?:\+\d{1,2}\s)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b')
        }
        
        def _process_payload(obj: Any, path: str = "") -> Any:
            if isinstance(obj, dict):
                shaped = {}
                for k, v in obj.items():
                    if k in effective_denies:
                        policy_reason = "Global Jurisdiction Policy" if k in global_denies else "Capability Contract prohibits exposure"
                        action = decision.action if decision.action != "ALLOW_WITH_REDACTION" else "STRIP"
                        field_decisions.append({
                            "field": f"{path}.{k}" if path else k,
                            "classification": "PII",
                            "requested_by": capability_id,
                            "policy": ", ".join(policy_bundle.applicable_policies) if policy_bundle else policy_reason,
                            "rule": decision.rule_applied,
                            "decision_id": decision.decision_id,
                            "decision": action,
                            "reason": policy_reason,
                            "resolver_version": decision.policy_version,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                        })
                    else:
                        shaped[k] = _process_payload(v, f"{path}.{k}" if path else k)
                return shaped
            elif isinstance(obj, list):
                return [_process_payload(item, path) for item in obj]
            elif isinstance(obj, str):
                original = obj
                for pii_type, pattern in PII_REGEXES.items():
                    if pattern.search(original):
                        original = pattern.sub(f"[{pii_type.upper()}]", original)
                        field_decisions.append({
                            "field": path,
                            "classification": f"PII_INLINE_{pii_type.upper()}",
                            "requested_by": capability_id,
                            "policy": ", ".join(policy_bundle.applicable_policies) if policy_bundle else "Global Policy",
                            "rule": decision.rule_applied,
                            "decision_id": decision.decision_id,
                            "decision": "REDACT_INLINE",
                            "reason": f"Detected inline PII ({pii_type})",
                            "resolver_version": decision.policy_version,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
                        })
                return original
            return obj
            
        shaped_payload = _process_payload(payload)
        
        # Secret Injection
        injected_secrets = []
        if decision.action == "ALLOW_WITH_SECRET_INJECTION" or decision.action == "ALLOW_WITH_REDACTION":
            for secret in contract.get("secret_injections", []):
                injected_key = f"_injected_{secret}"
                shaped_payload[injected_key] = f"vault_secret_{secret}_{tenant_jwt[:10]}"
                injected_secrets.append(secret)
                
        enforcement_details = {
            "decision_id": decision.decision_id,
            "field_decisions": field_decisions,
            "secret_injections": injected_secrets
        }
        
        return shaped_payload, enforcement_details
