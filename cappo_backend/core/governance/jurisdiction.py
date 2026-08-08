from typing import List, Dict, Any
from pydantic import BaseModel

class PolicyBundle(BaseModel):
    """
    A collection of applicable policies and data-shaping rules 
    derived from a specific jurisdiction and tenant profile.
    """
    jurisdiction: str
    applicable_policies: List[str]
    policy_version: str
    
    # Global overrides that supersede individual capability contracts
    global_denies_pii: List[str] = []
    require_audit: bool = True

class JurisdictionResolver:
    """
    Resolves Execution Identity and Tenant profile into a concrete Policy Bundle.
    This ensures execution is governed by regional laws (GDPR, PIPEDA, etc.).
    """
    
    def __init__(self):
        # Mock database of tenant profiles mapping to regions
        self.tenant_profiles = {
            "tenant-ca-123": {"region": "Canada", "sector": "Technology"},
            "tenant-eu-456": {"region": "Germany", "sector": "Healthcare"},
            "tenant-us-789": {"region": "California", "sector": "Finance"}
        }

    def resolve(self, execution_identity: str, tenant_id: str) -> PolicyBundle:
        """
        Determines the correct policy bundle based on the tenant's jurisdiction.
        """
        profile = self.tenant_profiles.get(tenant_id)
        if not profile:
            raise ValueError(f"Unknown tenant: {tenant_id}")
            
        region = profile.get("region")
        
        if region == "Canada":
            return PolicyBundle(
                jurisdiction="Canada",
                applicable_policies=["PIPEDA", "Law25"],
                policy_version="1.1.0",
                global_denies_pii=["ssn", "health_card", "financial_pin"]
            )
        elif region == "Germany":
            return PolicyBundle(
                jurisdiction="Germany",
                applicable_policies=["GDPR", "EU_AI_Act", "NIS2"],
                policy_version="2.0.0",
                # GDPR strict mode overrides capability requests for email/ip unless explicit
                global_denies_pii=["email", "ip_address", "location", "ssn", "health_card"]
            )
        elif region == "California":
            return PolicyBundle(
                jurisdiction="California",
                applicable_policies=["CCPA", "CPRA"],
                policy_version="1.0.0",
                global_denies_pii=["ssn", "precise_location"]
            )
        else:
            # Default fallback (Most restrictive)
            return PolicyBundle(
                jurisdiction="Unknown",
                applicable_policies=["Global_Strict"],
                policy_version="1.0.0",
                global_denies_pii=["email", "phone", "address", "ssn", "ip_address", "location"]
            )
