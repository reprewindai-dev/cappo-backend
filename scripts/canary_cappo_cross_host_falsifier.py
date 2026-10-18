#!/usr/bin/env python3
"""
Veklom CAPPO Cross-Host / Cloned-Runtime Falsifier (Canary Suite)
================================================================

Strict Invariant Under Test:
    possession(cloned_runtime_state, credentials) != authority_to_mutate_consequence
    Delta_C_stale == 0

Protocol Flow:
  1. Discovery: Verify veklom.governed-counter@v1 is registered.
  2. Mount: Create capability mount on Primary instance.
  3. Baseline Discriminator: Read independent target state via counter.read (C_0).
  4. Legitimate Mutation: Fire counter.increment on Primary -> state advances to C_1 = C_0 + 1.
  5. Clone State: Capture withheld mount snapshot (mount_id, token_id, nonce).
  6. Trigger Invalidation: Terminate mount on Primary (/v1/capability/mounts/{id}/terminate).
  7. Hostile Attack: Cloned instance fires counter.increment using held snapshot.
  8. Gate Enforcement: Gate must return decision == 'deny' (DENY_TERMINATED / stale).
  9. Independent Readback: Read target state via counter.read -> must remain C_1 (Delta C == 0).

Rules:
  - Non-destructive to production.
  - Zero mock fallback.
  - Independent readback decoupled from execution response.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


def make_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Dict[str, Any]]:
    req_headers = {
        "User-Agent": "Veklom-Canary-Falsifier/1.0",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content = resp.read().decode("utf-8")
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = {"raw": content}
            return status, parsed
    except urllib.error.HTTPError as e:
        status = e.code
        content = e.read().decode("utf-8")
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"raw": content}
        return status, parsed
    except Exception as e:
        return 0, {"error": str(e)}


def run_falsifier(
    base_url: str,
    auth_token: Optional[str] = None,
    api_key: Optional[str] = None,
    workspace_id: str = "w1",
    project_id: str = "canary-p1",
    resource_id: str = "canary-counter-001",
) -> Dict[str, Any]:
    print(f"=== Starting Veklom Cloned-Runtime Falsifier on {base_url} ===")
    print(f"Target: Workspace={workspace_id}, Project={project_id}, Resource={resource_id}")

    headers: Dict[str, str] = {
        "X-Workspace-ID": workspace_id,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if api_key:
        headers["X-API-Key"] = api_key

    telemetry: Dict[str, Any] = {
        "timestamp": time.time(),
        "target_url": base_url,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "resource_id": resource_id,
        "phases": {},
    }

    # Step 1: Package Discovery
    pkg_url = f"{base_url}/v1/capability/packages"
    status, pkgs = make_request(pkg_url, method="GET", headers=headers)
    print(f"[Phase 1: Discovery] GET {pkg_url} -> Status {status}")
    if status != 200 or not isinstance(pkgs, list):
        print(f"FAILED Phase 1: Unable to retrieve packages: {pkgs}")
        telemetry["phases"]["discovery"] = {"status": "FAILED", "response": pkgs}
        return telemetry

    pkg_ids = [p.get("id") for p in pkgs if isinstance(p, dict)]
    print(f"Registered Packages: {pkg_ids}")
    target_pkg = "veklom.governed-counter@v1"
    if target_pkg not in pkg_ids:
        print(f"FAILED: Package {target_pkg} not found in registry!")
        telemetry["phases"]["discovery"] = {"status": "FAILED", "found": pkg_ids}
        return telemetry
    telemetry["phases"]["discovery"] = {"status": "PASSED", "package": target_pkg}

    # Step 2: Capability Mount
    mount_url = f"{base_url}/v1/capability/mounts"
    mount_payload = {
        "package_ref": target_pkg,
        "execution_scope": {"workspace": workspace_id, "project": project_id},
        "requested_action_scope": {
            "reads": ["counter.read"],
            "writes": ["counter.increment"],
            "blocked": ["counter.reset"],
        },
        "ttl_seconds": 300,
    }
    status, mount_resp = make_request(mount_url, method="POST", headers=headers, data=mount_payload)
    print(f"[Phase 2: Mount] POST {mount_url} -> Status {status}")
    if status != 200 or mount_resp.get("decision") != "allow":
        print(f"FAILED Phase 2: Mount rejected: {mount_resp}")
        telemetry["phases"]["mount"] = {"status": "FAILED", "response": mount_resp}
        return telemetry

    mount_id = mount_resp["mount"]["id"]
    token = mount_resp["token"]
    print(f"Mount granted: ID={mount_id}, TokenID={token['token_id']}")
    telemetry["phases"]["mount"] = {"status": "PASSED", "mount_id": mount_id}

    # Step 3: Baseline Independent Readback
    readback_url = (
        f"{base_url}/v1/capability/targets/veklom.governed-counter.target/state?"
        + urllib.parse.urlencode({"resource": resource_id, "mount_id": mount_id})
    )
    status, readback_0 = make_request(readback_url, method="GET", headers=headers)
    print(f"[Phase 3: Baseline Readback] GET {readback_url} -> Status {status}")
    if status != 200:
        print(f"FAILED Phase 3: Baseline readback failed: {readback_0}")
        telemetry["phases"]["baseline_readback"] = {"status": "FAILED", "response": readback_0}
        return telemetry

    c0_value = readback_0.get("state", {}).get("value", 0)
    c0_version = readback_0.get("state", {}).get("version", 0)
    print(f"Baseline State: Value={c0_value}, Version={c0_version}")
    telemetry["phases"]["baseline_readback"] = {"status": "PASSED", "c0_value": c0_value}

    # Step 4: Legitimate Mutation on Primary
    exec_url = f"{base_url}/v1/capability/mounts/{mount_id}/execute"
    legit_payload = {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": "counter.increment",
        "target_ref": "veklom.governed-counter.target",
        "resource": resource_id,
        "arguments": {"by": 1},
        "operation_id": f"op-legit-{int(time.time())}",
    }
    status, legit_resp = make_request(exec_url, method="POST", headers=headers, data=legit_payload)
    print(f"[Phase 4: Legitimate Execution] POST {exec_url} -> Status {status}")
    if status != 200 or legit_resp.get("decision") != "allow":
        print(f"FAILED Phase 4: Legitimate execution denied: {legit_resp}")
        telemetry["phases"]["legit_exec"] = {"status": "FAILED", "response": legit_resp}
        return telemetry

    c1_value = legit_resp.get("consequence", {}).get("resulting_state", {}).get("value")
    print(f"Legitimate Mutation Allowed: Resulting Value={c1_value}")
    telemetry["phases"]["legit_exec"] = {"status": "PASSED", "c1_value": c1_value}

    # Step 5: Snapshot Cloned Runtime State (Simulating Zombie / Duplicate Instance)
    cloned_mount_id = mount_id
    cloned_token_id = token["token_id"]
    cloned_nonce = token["nonce"]

    # Step 6: Trigger Invalidation / Revocation on Primary
    term_url = f"{base_url}/v1/capability/mounts/{mount_id}/terminate"
    status, term_resp = make_request(
        term_url, method="POST", headers=headers, data={"reason": "epoch_advance_or_host_revocation"}
    )
    print(f"[Phase 6: Invalidation Trigger] POST {term_url} -> Status {status}")
    if status != 200 or term_resp.get("decision") != "allow":
        print(f"FAILED Phase 6: Termination failed: {term_resp}")
        telemetry["phases"]["termination"] = {"status": "FAILED", "response": term_resp}
        return telemetry
    telemetry["phases"]["termination"] = {"status": "PASSED"}

    # Step 7: Hostile Attack from Cloned Runtime Instance
    attack_payload = {
        "token_id": cloned_token_id,
        "nonce": cloned_nonce,
        "action": "counter.increment",
        "target_ref": "veklom.governed-counter.target",
        "resource": resource_id,
        "arguments": {"by": 1},
        "operation_id": f"op-zombie-attack-{int(time.time())}",
    }
    status, attack_resp = make_request(exec_url, method="POST", headers=headers, data=attack_payload)
    print(f"[Phase 7: Cloned Zombie Attack] POST {exec_url} -> Status {status}")
    print(f"Attack Response: {attack_resp}")

    # Check Fencing Rule
    is_denied = attack_resp.get("decision") == "deny" or status in (403, 409, 410)
    if not is_denied:
        print("CRITICAL FALSIFICATION VIOLATION: Cloned runtime mutation was ALLOWED!")
        telemetry["phases"]["hostile_attack"] = {"status": "FAILED", "response": attack_resp}
        return telemetry
    print("Gate Fencing Succeeded: Hostile mutation was strictly DENIED.")
    telemetry["phases"]["hostile_attack"] = {
        "status": "PASSED",
        "decision": attack_resp.get("decision"),
        "reason": attack_resp.get("reason"),
    }

    # Step 8: Independent Discriminator Readback (Machine Ground Truth)
    status, readback_final = make_request(readback_url, method="GET", headers=headers)
    print(f"[Phase 8: Independent Readback] GET {readback_url} -> Status {status}")
    final_value = readback_final.get("state", {}).get("value")
    final_version = readback_final.get("state", {}).get("version")
    print(f"Final Readback State: Value={final_value}, Version={final_version}")

    delta_c_stale = final_value - c1_value
    print(f"Calculated Delta C_stale = {delta_c_stale}")
    telemetry["phases"]["discriminator"] = {
        "status": "PASSED" if delta_c_stale == 0 else "FAILED",
        "initial_c0": c0_value,
        "legitimate_c1": c1_value,
        "final_readback": final_value,
        "delta_c_stale": delta_c_stale,
    }

    if delta_c_stale == 0:
        print("\n===========================================================")
        print("VERDICT: PROVEN UNDER TESTED CONDITIONS")
        print("possession(cloned_runtime_state) != authority(consequence)")
        print(f"Delta C_stale == 0 (Counter firmly locked at {final_value})")
        print("===========================================================\n")
        telemetry["verdict"] = "PROVEN_UNDER_TESTED_CONDITIONS"
    else:
        print("\n===========================================================")
        print("VERDICT: FALSIFIED (State mutated by unauthorized runtime)")
        print(f"Delta C_stale = {delta_c_stale}")
        print("===========================================================\n")
        telemetry["verdict"] = "FALSIFIED"

    return telemetry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Veklom Canary Cross-Host Falsifier")
    parser.add_argument("--url", default="http://127.0.0.1:8002", help="CAPPO base URL")
    parser.add_argument("--jwt", default=None, help="Bearer JWT token")
    parser.add_argument("--api-key", default=None, help="API Key")
    parser.add_argument("--workspace", default="canary-ws-1", help="Workspace ID")
    parser.add_argument("--project", default="canary-p1", help="Project ID")
    parser.add_argument("--resource", default="canary-counter-001", help="Counter resource ID")

    args = parser.parse_args()
    res = run_falsifier(
        base_url=args.url,
        auth_token=args.jwt,
        api_key=args.api_key,
        workspace_id=args.workspace,
        project_id=args.project,
        resource_id=args.resource,
    )
    with open("canary_falsifier_result.json", "w") as f:
        json.dump(res, f, indent=2)
    sys.exit(0 if res.get("verdict") == "PROVEN_UNDER_TESTED_CONDITIONS" else 1)
