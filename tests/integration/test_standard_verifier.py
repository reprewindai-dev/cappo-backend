import pytest
from cappo_backend.core.governance.standard_verifier import StandardVerifier
from cappo_backend.core.governance.context_shaper import ContextShaper

def test_standard_verifier_loads_standards():
    verifier = StandardVerifier()
    # It should load at least x402 and RFC9989_DMARC
    assert "x402" in verifier.standards
    assert "RFC9989" in verifier.standards
    
    x402_std = verifier.standards["x402"]
    assert x402_std["version"] == "1.0"
    
def test_standard_verifier_evaluates_compliance():
    verifier = StandardVerifier()
    
    # Mock an execution context where x402 fails but RFC9989 passes
    context = {
        "mock_compliance_x402": False,
        "mock_compliance_RFC9989": True
    }
    
    results = verifier.verify(["x402", "RFC9989", "unknown_standard"], context)
    
    assert len(results) == 3
    
    x402_result = next(r for r in results if r["id"] == "x402")
    assert x402_result["result"] == "FAIL"
    
    rfc_result = next(r for r in results if r["id"] == "RFC9989")
    assert rfc_result["result"] == "PASS"
    
    unknown_result = next(r for r in results if r["id"] == "unknown_standard")
    assert unknown_result["result"] == "NOT_FOUND"

def test_context_shaper_includes_standards_compliance():
    shaper = ContextShaper()
    
    payload = {
        "tenant": "test_tenant",
        "repository": "test_repo",
        "mock_compliance_x402": True,
        "mock_compliance_RFC9989": True
    }
    
    # blueprint.generate requires x402 and RFC9989
    shaped_payload, audit_record, action = shaper.shape_context(
        capability_id="blueprint.generate",
        payload=payload,
        tenant_jwt="mock_jwt"
    )
    
    assert "standards_compliance" in audit_record
    compliance = audit_record["standards_compliance"]
    
    assert len(compliance) == 2
    x402_compliance = next(c for c in compliance if c["id"] == "x402")
    assert x402_compliance["result"] == "PASS"
