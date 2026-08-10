from cappo_backend.core.governance.context_shaper import ContextShaper
from cappo_backend.core.governance.jurisdiction import JurisdictionResolver
from cappo_backend.core.governance.standard_verifier import StandardVerifier


def test_standard_verifier_loads_standards():
    verifier = StandardVerifier()
    assert "x402" in verifier.standards
    assert "RFC9989" in verifier.standards

    x402_std = verifier.standards["x402"]
    assert x402_std["version"] == "1.0"


def test_standard_verifier_evaluates_compliance():
    verifier = StandardVerifier()

    context = {
        "dmarc_tree_walk_performed": True,
        "np_tag_honored": True,
    }

    results = verifier.verify(["x402", "RFC9989", "unknown_standard"], context)

    assert len(results) == 3

    x402_result = next(r for r in results if r["id"] == "x402")
    assert x402_result["result"] == "NOT_VERIFIED"

    rfc_result = next(r for r in results if r["id"] == "RFC9989")
    assert rfc_result["result"] == "PASS"

    unknown_result = next(r for r in results if r["id"] == "unknown_standard")
    assert unknown_result["result"] == "NOT_FOUND"


def test_x402_receipt_presence_cannot_promote_verification():
    verifier = StandardVerifier()
    context = {
        "http_status_code": 402,
        "has_receipt_id": True,
    }

    result = verifier.verify(["x402"], context)[0]

    assert result["result"] == "NOT_VERIFIED"
    assert "cryptographic settlement verification is not integrated" in result["reason"]


def test_context_shaper_fails_closed_when_required_standard_is_unverified():
    shaper = ContextShaper()
    policy_bundle = JurisdictionResolver().resolve("exec_standard_test", "tenant-ca-123")
    assert policy_bundle.jurisdiction == "Canada"

    payload = {
        "tenant": "test_tenant",
        "repository": "test_repo",
        "http_status_code": 402,
        "has_receipt_id": True,
        "dmarc_tree_walk_performed": True,
        "np_tag_honored": True,
    }

    shaped_payload, audit_record, action = shaper.shape_context(
        capability_id="blueprint.generate",
        payload=payload,
        tenant_jwt="test-token",
        policy_bundle=policy_bundle,
    )

    compliance = audit_record["standards_compliance"]
    assert len(compliance) == 2

    x402_compliance = next(c for c in compliance if c["id"] == "x402")
    rfc_compliance = next(c for c in compliance if c["id"] == "RFC9989")
    assert x402_compliance["result"] == "NOT_VERIFIED"
    assert rfc_compliance["result"] == "PASS"

    # Canada/blueprint.generate is otherwise an allowing-with-redaction policy.
    # This FAIL_CLOSED therefore comes from the required-standard enforcement.
    assert action == "FAIL_CLOSED"
    assert audit_record["rule_applied"] == "Required-Standard-Verification-Failed:x402"
    assert audit_record["enforcement_decision"] == "FAIL_CLOSED"
    assert shaped_payload == {}
