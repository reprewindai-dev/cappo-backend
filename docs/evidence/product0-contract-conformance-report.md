# Veklom PRODUCT-0 Contract Conformance & CI Gate Report

**Date:** September 9, 2026  
**Status:** `LOCAL CONTRACT GATE: PASSED`  
**Next Gate:** `STATUS: READY FOR LIVE RUNTIME CONFORMANCE`  
**Target Specification:** OpenAPI 3.1.0 (`contracts/cappo-consequence-dispatch.openapi.yaml`) & AsyncAPI 3.0.0 (`contracts/cappo-consequence-events.asyncapi.yaml`)  

---

## 1. Executive Summary & Architecture
This report documents the machine-enforceable **PRODUCT-0 Contract Conformance Gate** (`scripts/verify_product0_contract.py`). The gate binds the proven PRODUCT-0 invariants (Tests A–L) directly to the OpenAPI 3.1.0 and AsyncAPI 3.0.0 specifications, preventing contract drift or regressions before code can be merged into GitHub `main`.

```
Proven Invariants (Tests A–L)
         ↓
Normative Specs (OpenAPI 3.1.0 & AsyncAPI 3.0.0)
         ↓
Executable Contract Suite (tests/contract/ — 11/11 Passed)
         ↓
Local Contract Gate (LOCAL CONTRACT GATE: PASSED)
         ↓
Live Port-8002 Hostile Battery & Raw Evidence Seal
         ↓
MERGE GATE SATISFIED
```

---

## 2. Canonical Specification Artifacts
The normative contracts are frozen under `contracts/`:
* **`contracts/cappo-consequence-dispatch.openapi.yaml`**: OpenAPI 3.1.0 spec governing `/v1/mounts`, `/v1/consequence/dispatch`, `/v1/consequence/reconcile`, and error schema mapping.
* **`contracts/cappo-consequence-events.asyncapi.yaml`**: AsyncAPI 3.0.0 spec governing streaming channels (`cappo.wal.events`, `cappo.vre.substrate`, `cappo.reconciliation.events`).
* **`contracts/cappo-consequence-spec-corrections.md`**: Specification corrections recording exact denial class mappings, status code semantics, and authority custody rules.

---

## 3. Contract Test Suite Results (`11 / 11` Executed, `0` Failures, `0` Errors)

| Test Module | Rule / Invariant Tested | Execution Outcome |
| :--- | :--- | :--- |
| **`test_dispatch_contract.py`** | Denial Class Mapping (`ReplayDeniedError`, `ExecutionIdMismatchError`, `EnvelopeSubstitutionError`) | `GREEN / PASSED` |
| **`test_dispatch_contract.py`** | HTTP 423 Locked for `OUTCOME_UNKNOWN` Retries | `GREEN / PASSED` |
| **`test_dispatch_contract.py`** | HTTP 503 Reserved Strictly for Target Infrastructure Failure | `GREEN / PASSED` |
| **`test_dispatch_contract.py`** | OpenAPI Dispatch Response Schema Compliance | `GREEN / PASSED` |
| **`test_reconciliation_contract.py`** | Server-Bound Observer Endpoint Isolation (Ignores Caller URLs) | `GREEN / PASSED` |
| **`test_reconciliation_contract.py`** | Unregistered Target Action Fail-Closed Rejection | `GREEN / PASSED` |
| **`test_authority_custody_contract.py`** | Authority Custody (No Raw Biscuit / Key Leaks in Response) | `GREEN / PASSED` |
| **`test_authority_custody_contract.py`** | Simulated Raw Biscuit Leak Detection | `GREEN / PASSED` |
| **`test_authority_custody_contract.py`** | Ingress `Veklom-Authority` Header Stripping | `GREEN / PASSED` |
| **`test_event_contract.py`** | AsyncAPI WAL Event Payload Schema Compliance | `GREEN / PASSED` |
| **`test_event_contract.py`** | Missing Required WAL Field Schema Rejection | `GREEN / PASSED` |

---

## 4. Machine-Readable Verification Gate (`scripts/verify_product0_contract.py`)
The runner executes dynamically relative to the repository root (`REPO_ROOT`):
* **Strict Test Count Assertion**: Asserts `testsRun == 11`, `failures == 0`, `errors == 0`.
* **Dynamic Rule Status Mapping**: Evaluates each rule against actual test pass/fail objects rather than printing static strings.
* **JSON Evidence Export**: Writes machine-readable JSON summary to `docs/evidence/contract_gate_result.json`.

```json
{
  "gate_name": "product0_contract_conformance_gate",
  "status": "LOCAL_PASSED_READY_FOR_LIVE",
  "tests_run": 11,
  "expected_tests": 11,
  "failures": 0,
  "errors": 0,
  "skipped": 0,
  "invariants_validated": true,
  "openapi_file": "contracts/cappo-consequence-dispatch.openapi.yaml",
  "asyncapi_file": "contracts/cappo-consequence-events.asyncapi.yaml",
  "live_runtime_proof_pending": true
}
```

---

## 5. CI Workflow Sequence (`.github/workflows/product0-contract-gate.yml`)

```
Step 1: Locate Repo Root & Parse Specifications
Step 2: Validate x-veklom-invariants Rules
Step 3: Run 11 Contract Tests (Require exactly 11/11)
        ↓ PASS
LOCAL CONTRACT GATE: PASSED (STATUS: READY FOR LIVE RUNTIME CONFORMANCE)
        ↓
Step 4: Launch CAPPO Integration Service on Port 8002
Step 5: Execute Hostile Port-8002 Contract Battery
Step 6: Seal Raw Responses & Receipts in docs/evidence/
        ↓ PASS
MERGE GATE: SATISFIED
```
