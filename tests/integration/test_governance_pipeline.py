import pytest
from cappo_backend.core.governance.jurisdiction import JurisdictionResolver
from cappo_backend.core.governance.context_shaper import ContextShaper

def test_governance_pipeline_canadian_tenant():
    # 1. Initialize primitives
    resolver = JurisdictionResolver()
    shaper = ContextShaper()
    
    # 2. Mock an incoming execution payload
    tenant_id = "tenant-ca-123"
    tenant_jwt = "jwt_signature_xyz"
    capability_id = "blueprint.generate"
    payload = {
        "tenant_id": tenant_id,
        "repository_url": "https://github.com/veklom/test",
        "github_username": "anthony",
        "email": "anthony@veklom.com",
        "ssn": "000-00-0000",
        "health_card": "1234567890" # Specifically denied by Canadian jurisdiction, not just capability
    }
    
    # 3. Resolve Jurisdiction
    policy_bundle = resolver.resolve("exec_id_999", tenant_id)
    
    # Assert jurisdiction resolution correctly identified Canada
    assert policy_bundle.jurisdiction == "Canada"
    assert "PIPEDA" in policy_bundle.applicable_policies
    assert "health_card" in policy_bundle.global_denies_pii
    
    # 4. Shape the Context
    shaped_payload, audit = shaper.shape_context(capability_id, payload, tenant_jwt, policy_bundle)
    
    # 5. Assert shaping logic (Capability rules + Jurisdiction rules)
    # Email and SSN are removed by capability contract
    assert "email" not in shaped_payload
    assert "ssn" not in shaped_payload
    
    # Health card is removed by jurisdiction policy
    assert "health_card" not in shaped_payload
    
    # Github username is allowed
    assert "github_username" in shaped_payload
    
    # Github PAT is injected
    assert "_injected_github_pat" in shaped_payload
    
    # 6. Assert Audit Schema explicitly tracks jurisdiction
    assert audit["jurisdiction"] == "Canada"
    assert audit["applicable_policies"] == ["PIPEDA", "Law25"]
    assert "health_card" in audit["fields_removed"]
    assert "email" in audit["fields_removed"]
    assert "github_pat" in audit["secret_injections"]

if __name__ == "__main__":
    pytest.main(["-v", __file__])
