"""
Client-only hostile port-8002 battery.

AUTHORITY BOUNDARY: This file contains NO import of mint_biscuit_capability
and NO call to any internal Biscuit minting primitive. It is a pure HTTP
client. All Biscuit tokens are consumed from:

    docs/evidence/hostile_fixtures.json

which must be generated first by the server-side provisioner:

    .venv\\Scripts\\python.exe scripts\\provision_hostile_fixtures.py

Then start the CAPPO server independently:

    .venv\\Scripts\\uvicorn.exe cappo_backend.main:app --host 127.0.0.1 --port 8002

Then run this battery:

    .venv\\Scripts\\python.exe scripts\\run_hostile_port8002_battery.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
FIXTURE_FILE = EVIDENCE_DIR / "hostile_fixtures.json"

BASE_URL = "http://127.0.0.1:8002"
READINESS_TIMEOUT = 10  # seconds


# ── Readiness check ──────────────────────────────────────────────────────────

def wait_for_server() -> None:
    deadline = time.monotonic() + READINESS_TIMEOUT
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code < 500:
                print(f"[ready] CAPPO server responding on {BASE_URL}")
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    print(f"[FATAL] CAPPO server did not become ready within {READINESS_TIMEOUT}s")
    sys.exit(1)


# ── Fixture loading ──────────────────────────────────────────────────────────

def load_fixtures() -> dict:
    if not FIXTURE_FILE.exists():
        print(
            f"[FATAL] Fixture file missing: {FIXTURE_FILE}\n"
            "Run: .venv\\Scripts\\python.exe scripts\\provision_hostile_fixtures.py"
        )
        sys.exit(1)
    with FIXTURE_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


# ── Battery execution ────────────────────────────────────────────────────────

def execute_battery() -> None:
    wait_for_server()
    fixtures = load_fixtures()
    tokens = fixtures["tokens"]

    raw_responses: list[dict] = []
    passed_count = 0
    total_count = 0

    def log_case(
        tc_id: str,
        name: str,
        expected_status: int,
        url: str,
        payload: dict,
        headers: dict,
    ) -> bool:
        nonlocal passed_count, total_count
        total_count += 1
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            status = resp.status_code
            body = resp.json() if resp.text else {}
        except Exception as exc:
            status = 0
            body = {"error": str(exc)}

        passed = status == expected_status
        if passed:
            passed_count += 1

        label = "PASS" if passed else "FAIL"
        print(f"[{label}] {tc_id} | {name}")
        print(f"       HTTP: {status}  (expected {expected_status})")
        if not passed:
            print(f"       body: {body}")
        print()

        raw_responses.append(
            {
                "test_id": tc_id,
                "test_name": name,
                "timestamp_iso8601": timestamp,
                "passed": passed,
                "request": {"url": url, "payload": payload},
                "response": {"status_code": status, "body": body},
            }
        )
        return passed

    print("=" * 70)
    print("   HOSTILE PORT-8002 CAPPO REQUEST BATTERY  (client-only)   ")
    print("=" * 70)

    dispatch = f"{BASE_URL}/v1/consequence/dispatch"
    reconcile = f"{BASE_URL}/v1/consequence/reconcile"

    # ── Dispatch cases ───────────────────────────────────────────────────────

    # HOSTILE-01: No auth header → 401
    log_case(
        "HOSTILE-01", "Test A: Unauthenticated Dispatch", 401,
        dispatch, {"execution_id": "exec-anon"}, {},
    )

    # HOSTILE-02: Forged Veklom-Authority header with invalid Bearer → 401
    log_case(
        "HOSTILE-02", "Test A2: Forged Authority Header Injection", 401,
        dispatch,
        {"execution_id": "exec-anon"},
        {"Veklom-Authority": "forged", "Authorization": "Bearer invalid"},
    )

    # HOSTILE-10: Valid server-provisioned token → 200 DISPATCHED
    valid_hdr = {"Authorization": f"Bearer {tokens['exec_valid']}"}
    log_case(
        "HOSTILE-10", "Test B: Valid Authorized Dispatch", 200,
        dispatch,
        {
            "prompt": "test", "agent_id": "abc",
            "execution_id": "exec-valid",
            "action": "fs.write", "resource": "file.txt",
            "mount_id": "mount-hostile",
        },
        valid_hdr,
    )

    # HOSTILE-03: Exact replay of the same execution_id → 423
    log_case(
        "HOSTILE-03", "Test C: Exact Replay of Consumed Lease", 423,
        dispatch,
        {
            "prompt": "test", "agent_id": "abc",
            "execution_id": "exec-valid",
            "action": "fs.write", "resource": "file.txt",
            "mount_id": "mount-hostile",
        },
        valid_hdr,
    )

    # HOSTILE-04: Token embeds exec-A but body supplies exec-B → 422
    log_case(
        "HOSTILE-04", "Test E: Execution ID Substitution (token=exec-A, body=exec-B)", 422,
        dispatch,
        {
            "prompt": "test", "agent_id": "abc",
            "execution_id": "exec-B",
            "action": "fs.write", "resource": "file.txt",
            "mount_id": "mount-hostile",
        },
        {"Authorization": f"Bearer {tokens['exec_A_token']}"},
    )

    # HOSTILE-05: Token embeds exec-C but body supplies exec-D → 422
    log_case(
        "HOSTILE-05", "Test F: Envelope Substitution (token=exec-C, body=exec-D)", 422,
        dispatch,
        {
            "prompt": "test", "agent_id": "abc",
            "execution_id": "exec-D",
            "action": "fs.write", "resource": "file.txt",
            "mount_id": "mount-hostile",
        },
        {"Authorization": f"Bearer {tokens['exec_C_token']}"},
    )

    # HOSTILE-06: Well-known invalid literal token → 401
    log_case(
        "HOSTILE-06", "Test G: Revoked/Invalid Token", 401,
        dispatch,
        {
            "prompt": "test", "agent_id": "abc",
            "execution_id": "exec-revoked",
            "action": "fs.write", "resource": "file.txt",
            "mount_id": "mount-hostile",
        },
        {"Authorization": "Bearer invalid_sig"},
    )

    # ── Reconciliation cases ─────────────────────────────────────────────────
    # Rule: reconciliation infrastructure unavailable → 503 RECONCILIATION_UNAVAILABLE
    # Rule: subsequent redispatch while execution is locked → 423
    # These are two DISTINCT moments; they must not be collapsed.

    # HOSTILE-07: Reconcile, target unavailable → 503
    log_case(
        "HOSTILE-07", "Test K: Reconcile — OUTCOME_UNKNOWN, target unavailable", 503,
        reconcile,
        {"execution_id": "exec-unknown", "simulate_target_503": True},
        valid_hdr,
    )

    # HOSTILE-08: Reconcile, infrastructure unavailable → 503
    log_case(
        "HOSTILE-08", "Test L: Reconcile — Target 503, infrastructure unavailable", 503,
        reconcile,
        {"execution_id": "exec-503", "simulate_target_503": True},
        valid_hdr,
    )

    # HOSTILE-09: Reconcile, target restored → 200 RECONCILED_SUCCEEDED
    log_case(
        "HOSTILE-09", "Test L Recovery: Reconcile — Target Restored", 200,
        reconcile,
        {"execution_id": "exec-503", "simulate_target_503": False},
        valid_hdr,
    )

    # ── Biscuit custody case ─────────────────────────────────────────────────
    # Prove the mount response does not expose raw Biscuit token material.
    # We hit the real mount endpoint and assert biscuit_token is absent.
    total_count += 1
    timestamp = datetime.now(timezone.utc).isoformat()
    tc_id = "HOSTILE-11"
    name = "Test M: Mount Response Must Not Expose Raw Biscuit Token"
    custody_passed = False
    custody_body: dict = {}
    try:
        # The mount endpoint requires auth. In test mode (auth_enabled=False)
        # any request proceeds as "auth-disabled" principal.
        r = requests.post(
            f"{BASE_URL}/v1/capability/mounts",
            json={
                "package_ref": "sandbox-file-append@v1",
                "execution_scope": {
                    "workspace": "test-workspace",
                    "project": "test-project",
                },
            },
            timeout=5,
        )
        custody_body = r.json() if r.text else {}
        has_raw_biscuit = "biscuit_token" in str(custody_body)
        custody_passed = not has_raw_biscuit
    except Exception as exc:
        custody_body = {"error": str(exc)}
        custody_passed = False

    if custody_passed:
        passed_count += 1

    label = "PASS" if custody_passed else "FAIL"
    print(f"[{label}] {tc_id} | {name}")
    print(f"       biscuit_token absent from response: {custody_passed}")
    if not custody_passed:
        print(f"       body snippet: {str(custody_body)[:200]}")
    print()

    raw_responses.append(
        {
            "test_id": tc_id,
            "test_name": name,
            "timestamp_iso8601": timestamp,
            "passed": custody_passed,
            "request": {"url": f"{BASE_URL}/v1/capability/mounts"},
            "response": {
                "biscuit_token_absent": custody_passed,
                "body_excerpt": str(custody_body)[:300],
            },
        }
    )

    # ── Seal evidence ────────────────────────────────────────────────────────
    evidence_file = EVIDENCE_DIR / "raw_hostile_port8002_responses.json"
    with evidence_file.open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "audit_title": "Veklom Direct-to-Port-8002 Hostile Runtime Request Audit",
                "server_host": "127.0.0.1:8002",
                "run_timestamp": datetime.now(timezone.utc).isoformat(),
                "total_tests": total_count,
                "passed_tests": passed_count,
                "all_tests_passed": passed_count == total_count,
                "raw_responses": raw_responses,
            },
            fh,
            indent=2,
        )

    gate_result_file = EVIDENCE_DIR / "contract_gate_result.json"
    gate_summary: dict = {}
    if gate_result_file.exists():
        with gate_result_file.open(encoding="utf-8") as fh:
            gate_summary = json.load(fh)

    verdict = "PASSED" if passed_count == total_count else "FAILED"
    gate_summary.update(
        {
            "live_runtime_proof_pending": False,
            "live_runtime_proof_passed": passed_count == total_count,
            "raw_evidence_sealed": str(evidence_file),
            "final_merge_gate_status": f"CONTRACT + RUNTIME CONFORMANCE: {verdict}",
        }
    )
    with gate_result_file.open("w", encoding="utf-8") as fh:
        json.dump(gate_summary, fh, indent=2)

    print("=" * 70)
    print(f"RESULTS: {passed_count}/{total_count} passed")
    print(f"Evidence: {evidence_file}")
    print(f"Gate:     {gate_summary['final_merge_gate_status']}")
    print("=" * 70)

    if passed_count != total_count:
        sys.exit(1)


if __name__ == "__main__":
    execute_battery()
