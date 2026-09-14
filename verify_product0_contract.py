#!/usr/bin/env python3
"""
scripts/verify_product0_contract.py — Repository-Safe PRODUCT-0 Contract Conformance Gate

Executes local contract conformance verification for OpenAPI 3.1.0 & AsyncAPI 3.0.0 specs,
validates x-veklom-invariants, dynamically maps test results, requires exact 11/11 test count,
and exports machine-readable JSON results.
"""

import sys
import json
import unittest
from pathlib import Path

# A. Dynamically locate repository root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Optional YAML parser import
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_yaml_spec(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Contract file missing: {path}")
    content = path.read_text(encoding="utf-8")
    if HAS_YAML:
        return yaml.safe_load(content)
    # Simple lightweight fallback parser for basic YAML validation
    return {"raw_text": content, "exists": True}


def run_contract_conformance_gate():
    print("\031[1m\033[96m======================================================================\033[0m")
    print("\033[1m\033[96m    VEKLOM PRODUCT-0 CONTRACT CONFORMANCE GATE (LOCAL GATEWAY)       \033[0m")
    print("\033[1m\033[96m======================================================================\033[0m\n")

    # B & C. Parse corrected contract files relative to REPO_ROOT
    openapi_path = REPO_ROOT / "contracts" / "cappo-consequence-dispatch.openapi.yaml"
    asyncapi_path = REPO_ROOT / "contracts" / "cappo-consequence-events.asyncapi.yaml"

    openapi_spec = load_yaml_spec(openapi_path)
    asyncapi_spec = load_yaml_spec(asyncapi_path)

    # D. Validate x-veklom-invariants presence
    invariants_validated = False
    if HAS_YAML and isinstance(openapi_spec, dict):
        dispatch_path = openapi_spec.get("paths", {}).get("/v1/consequence/dispatch", {}).get("post", {})
        invariants = dispatch_path.get("x-veklom-invariants", [])
        if len(invariants) >= 3:
            invariants_validated = True
    else:
        # Fallback keyword check if pyyaml isn't available
        raw_openapi = openapi_path.read_text(encoding="utf-8")
        if "x-veklom-invariants" in raw_openapi and "EXACT_DENIAL_CLASS_MAPPING" in raw_openapi:
            invariants_validated = True

    # E. Import and execute the 11 contract tests
    from tests.contract.test_dispatch_contract import TestDispatchContract
    from tests.contract.test_reconciliation_contract import TestReconciliationContract
    from tests.contract.test_authority_custody_contract import TestAuthorityCustodyContract
    from tests.contract.test_event_contract import TestEventContract

    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestDispatchContract))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestReconciliationContract))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestAuthorityCustodyContract))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestEventContract))

    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)

    # F. Require exactly 11/11 tests, 0 failures, 0 errors, 0 skipped
    EXPECTED_TEST_COUNT = 11
    tests_run = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)

    success = (
        tests_run == EXPECTED_TEST_COUNT
        and failures == 0
        and errors == 0
        and skipped == 0
        and invariants_validated
    )

    # Build dynamic rule status mapping based on actual test outcome
    # We must ensure the expected named test exists, ran, passed, and was not skipped.
    def get_all_test_ids(test_suite):
        ids = []
        for test in test_suite:
            if isinstance(test, unittest.TestSuite):
                ids.extend(get_all_test_ids(test))
            else:
                ids.append(test.id())
        return ids

    run_test_ids = get_all_test_ids(suite)
    failed_test_names = [f[0].id() for f in result.failures] + [e[0].id() for e in result.errors] + [s[0].id() for s in result.skipped]

    rules = [
        ("OpenAPI Response Schema Compliance", "test_openapi_schema_compliance_on_success"),
        ("HTTP 423 Locked / 503 Status Code Semantics", "test_http_423_locked_enforced_for_outcome_unknown_retries"),
        ("Denial Class Mapping (Replay, Substitution, Revocation)", "test_denial_classes_map_to_specified_errors"),
        ("Server-Bound Observer Endpoint Isolation", "test_client_cannot_inject_arbitrary_reconciliation_url"),
        ("Browser Response Authority Custody (No Raw Leaks)", "test_response_does_not_leak_raw_biscuit"),
        ("Ingress Veklom-Authority Header Stripping", "test_untrusted_client_authority_header_is_stripped"),
        ("AsyncAPI WAL & VRE Event Schema Conformance", "test_wal_event_schema_conformance"),
        ("Hostile x-veklom-invariants Specification Rules", invariants_validated)
    ]

    print("\n======================================================================")
    print("\033[1mPRODUCT-0 CONTRACT CONFORMANCE GATE SUMMARY\033[0m")
    print("======================================================================")

    for rule_name, test_ref in rules:
        if isinstance(test_ref, bool):
            passed = test_ref
        else:
            # Must exist in the suite, and must not be in failures/errors/skipped
            test_ran = any(test_ref in run_id for run_id in run_test_ids)
            test_failed = any(test_ref in f_name for f_name in failed_test_names)
            passed = test_ran and not test_failed
        
        status_label = "\033[92mGREEN / PASSED\033[0m" if passed else "\033[91mRED / FAILED\033[0m"
        print(f"  \033[1m{rule_name:<50}\033[0m | {status_label}")

    print("======================================================================")
    print(f"  Tests Executed : {tests_run} / {EXPECTED_TEST_COUNT}")
    print(f"  Failures       : {failures}")
    print(f"  Errors         : {errors}")
    print(f"  Skipped        : {skipped}")
    print("======================================================================")

    # Calibrated Gate Status Wording
    if success:
        print("\033[1m\033[92mLOCAL CONTRACT GATE: PASSED\033[0m")
        print("\033[1m\033[92mSTATUS: READY FOR LIVE RUNTIME CONFORMANCE\033[0m")
    else:
        print("\033[1m\033[91mLOCAL CONTRACT GATE: FAILED\033[0m")
        print("\033[1m\033[91mSTATUS: DRIFT DETECTED — MERGE BLOCKED\033[0m")
    print("======================================================================\n")

    # G. Export machine-readable JSON result
    evidence_dir = REPO_ROOT / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    json_path = evidence_dir / "contract_gate_result.json"

    gate_result_json = {
        "gate_name": "product0_contract_conformance_gate",
        "status": "LOCAL_PASSED_READY_FOR_LIVE" if success else "FAILED",
        "tests_run": tests_run,
        "expected_tests": EXPECTED_TEST_COUNT,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "invariants_validated": invariants_validated,
        "openapi_file": str(openapi_path.relative_to(REPO_ROOT)),
        "asyncapi_file": str(asyncapi_path.relative_to(REPO_ROOT)),
        "live_runtime_proof_pending": True
    }

    json_path.write_text(json.dumps(gate_result_json, indent=2), encoding="utf-8")
    print(f"Machine-readable result exported to: {json_path}")

    # H. Exit non-zero on any failure or count drift
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    run_contract_conformance_gate()
