#!/usr/bin/env python3
"""Client-only PRODUCT-0 hostile HTTP battery.

This runner NEVER starts or binds a server. It requires an independently
running CAPPO HTTP service and deterministic test fixtures supplied via env.
It exits non-zero on readiness failure, missing fixtures, status drift, or
body/error-class drift.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get("CAPPO_TEST_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
TOKEN = os.environ.get("CAPPO_TEST_BEARER_TOKEN")


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def http_json(method, path, payload=None, headers=None, timeout=5):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req_headers = {"Accept": "application/json"}
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = Request(BASE_URL + path, data=data, headers=req_headers, method=method)
    started_at = now_iso()
    try:
        with urlopen(req, timeout=timeout) as resp:
            body_raw = resp.read().decode("utf-8")
            body = json.loads(body_raw) if body_raw else {}
            return resp.status, dict(resp.headers), body, started_at, now_iso()
    except HTTPError as e:
        body_raw = e.read().decode("utf-8")
        try:
            body = json.loads(body_raw) if body_raw else {}
        except Exception:
            body = {"raw": body_raw}
        return e.code, dict(e.headers), body, started_at, now_iso()


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required deterministic fixture env var is missing: {name}")
    return value


def readiness_check():
    candidates = ["/health", "/healthz", "/v1/health"]
    last_error = None
    for path in candidates:
        try:
            status, headers, body, _, _ = http_json("GET", path, timeout=2)
            if 200 <= status < 500:
                server = headers.get("Server", "")
                if "BaseHTTP/" in server:
                    raise RuntimeError("Refusing simulated BaseHTTP server; expected canonical CAPPO runtime")
                return {"path": path, "status": status, "server": server, "body": body}
        except (URLError, RuntimeError) as exc:
            last_error = exc
    raise RuntimeError(f"CAPPO readiness failed at {BASE_URL}: {last_error}")


def auth_headers(extra=None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if extra:
        headers.update(extra)
    return headers


def run_case(case):
    status, headers, body, started_at, finished_at = http_json(
        "POST", case["path"], case.get("payload"), case.get("headers"), timeout=5
    )
    passed = status == case["expected_status"]
    if case.get("expected_error_class") is not None:
        passed = passed and body.get("error_class") == case["expected_error_class"]
    if case.get("expected_status_value") is not None:
        passed = passed and body.get("status") == case["expected_status_value"]
    return {
        "id": case["id"],
        "name": case["name"],
        "passed": passed,
        "request_started_at": started_at,
        "response_finished_at": finished_at,
        "request": {"path": case["path"], "payload": case.get("payload", {})},
        "response": {"status_code": status, "headers": headers, "body": body},
        "expected": {
            "status_code": case["expected_status"],
            "error_class": case.get("expected_error_class"),
            "status": case.get("expected_status_value"),
        },
    }


def main():
    global TOKEN
    readiness = readiness_check()
    TOKEN = require_env("CAPPO_TEST_BEARER_TOKEN")

    fixtures = {
        "consumed_lease": require_env("CAPPO_TEST_CONSUMED_LEASE_ID"),
        "revoked_lease": require_env("CAPPO_TEST_REVOKED_LEASE_ID"),
        "valid_lease": require_env("CAPPO_TEST_VALID_LEASE_ID"),
        "valid_execution": require_env("CAPPO_TEST_VALID_EXECUTION_ID"),
        "mismatch_lease": require_env("CAPPO_TEST_MISMATCH_LEASE_ID"),
        "mismatch_execution": require_env("CAPPO_TEST_MISMATCH_EXECUTION_ID"),
        "locked_execution": require_env("CAPPO_TEST_LOCKED_EXECUTION_ID"),
        "reconcile_execution": require_env("CAPPO_TEST_RECONCILE_EXECUTION_ID"),
    }

    cases = [
        {"id": "HOSTILE-01", "name": "Unauthenticated dispatch", "path": "/v1/consequence/dispatch", "payload": {"execution_id": "unauthenticated"}, "headers": {}, "expected_status": 401, "expected_error_class": "AuthorityDeniedError"},
        {"id": "HOSTILE-02", "name": "Client Veklom-Authority injection", "path": "/v1/consequence/dispatch", "payload": {"execution_id": "forged"}, "headers": {"Veklom-Authority": "FORGED_CLIENT_AUTHORITY"}, "expected_status": 401, "expected_error_class": "AuthorityDeniedError"},
        {"id": "HOSTILE-03", "name": "Consumed lease replay", "path": "/v1/consequence/dispatch", "payload": {"execution_id": fixtures["valid_execution"], "lease_id": fixtures["consumed_lease"]}, "headers": auth_headers(), "expected_status": 423, "expected_error_class": "ReplayDeniedError"},
        {"id": "HOSTILE-04", "name": "Lease/execution substitution", "path": "/v1/consequence/dispatch", "payload": {"execution_id": fixtures["mismatch_execution"], "lease_id": fixtures["mismatch_lease"]}, "headers": auth_headers(), "expected_status": 422, "expected_error_class": "ExecutionIdMismatchError"},
        {"id": "HOSTILE-06", "name": "Revoked lease", "path": "/v1/consequence/dispatch", "payload": {"execution_id": fixtures["valid_execution"], "lease_id": fixtures["revoked_lease"]}, "headers": auth_headers(), "expected_status": 403, "expected_error_class": "AuthorityDeniedError"},
        {"id": "HOSTILE-07", "name": "OUTCOME_UNKNOWN redispatch lock", "path": "/v1/consequence/dispatch", "payload": {"execution_id": fixtures["locked_execution"], "lease_id": fixtures["valid_lease"]}, "headers": auth_headers(), "expected_status": 423, "expected_error_class": "RetryLockedError"},
        {"id": "HOSTILE-10", "name": "Valid authorized consequence", "path": "/v1/consequence/dispatch", "payload": {"execution_id": fixtures["valid_execution"], "lease_id": fixtures["valid_lease"]}, "headers": auth_headers(), "expected_status": 200, "expected_error_class": None},
    ]

    results = [run_case(case) for case in cases]
    artifact = {
        "audit_title": "Veklom client-only direct-to-port-8002 hostile runtime audit",
        "base_url": BASE_URL,
        "readiness": readiness,
        "executed_at": now_iso(),
        "total_tests": len(results),
        "passed_tests": sum(1 for r in results if r["passed"]),
        "all_tests_passed": all(r["passed"] for r in results),
        "results": results,
    }
    out = EVIDENCE_DIR / "raw_hostile_port8002_responses.json"
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"evidence": str(out), "passed": artifact["passed_tests"], "total": artifact["total_tests"]}, indent=2))
    if not artifact["all_tests_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
