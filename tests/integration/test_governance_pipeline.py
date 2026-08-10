import pytest

from cappo_backend.core.governance.context_shaper import ContextShaper
from cappo_backend.core.governance.jurisdiction import JurisdictionResolver


def test_governance_pipeline_canadian_tenant_fails_closed_without_required_evidence():
    resolver = JurisdictionResolver()
    shaper = ContextShaper()

    tenant_id = "tenant-ca-123"
    tenant_jwt = "jwt_signature_xyz"
    capability_id = "blueprint.generate"
    payload = {
        "tenant_id": tenant_id,
        "repository_url": "https://github.com/veklom/test",
        "github_username": "anthony",
        "email": "anthony@veklom.com",
        "ssn": "000-00-0000",
        "health_card": "1234567890",
    }

    policy_bundle = resolver.resolve("exec_id_999", tenant_id)
    assert policy_bundle.jurisdiction == "Canada"
    assert "PIPEDA" in policy_bundle.applicable_policies
    assert "health_card" in policy_bundle.global_denies_pii

    shaped_payload, audit, decision = shaper.shape_context(
        capability_id,
        payload,
        tenant_jwt,
        policy_bundle,
    )

    # The Canada policy would otherwise allow with redaction, but this capability
    # requires x402 + RFC9989. Missing/unverified required evidence is authoritative.
    assert decision == "FAIL_CLOSED"
    assert shaped_payload == {}
    assert audit["jurisdiction"] == "Canada"
    assert audit["applicable_policies"] == ["PIPEDA", "Law25"]
    assert audit["enforcement_decision"] == "FAIL_CLOSED"
    assert audit["rule_applied"].startswith("Required-Standard-Verification-Failed:")

    compliance = {item["id"]: item for item in audit["standards_compliance"]}
    assert compliance["x402"]["result"] == "NOT_VERIFIED"
    assert compliance["RFC9989"]["result"] != "PASS"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
