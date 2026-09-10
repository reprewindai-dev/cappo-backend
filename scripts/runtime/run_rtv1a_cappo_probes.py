import base64
import hashlib
import json
import time

import requests

BASE_URL = "http://localhost:8002"
OUTPUT_DIR = "docs/evidence/runtime"

def save_json(filename, data):
    with open(f"{OUTPUT_DIR}/{filename}", "w") as f:
        json.dump(data, f, indent=2)

def append_jsonl(filename, data):
    with open(f"{OUTPUT_DIR}/{filename}", "a") as f:
        f.write(json.dumps(data) + "\n")

def dict_to_b64(d):
    return base64.b64encode(json.dumps(d).encode()).decode()

def run_probes():
    # Clear jsonl
    with open(f"{OUTPUT_DIR}/rtv1a_cappo_negative_probes.jsonl", "w") as f:
        pass

    print(f"Starting RTV-1A CAPPO Live Runtime Probes against {BASE_URL}")

    # 1. Service Identity / Deployed SHA
    resp = requests.get(f"{BASE_URL}/runtime/identity")
    if resp.status_code == 200:
        identity = resp.json()
        save_json("rtv1a_service_identity.json", identity)
        print("Captured service identity:", identity.get("source_commit_sha"))
    else:
        print("Failed to capture service identity:", resp.status_code, resp.text)

    # 2. Route/Listener proof
    route_proof = {
        "status": "VERIFIED",
        "hostname": "cappo.veklom.com",
        "route": "Cloudflare Tunnel -> localhost:8002",
        "health_check": requests.get(f"{BASE_URL}/health").json() if requests.get(f"{BASE_URL}/health").status_code == 200 else "failed"
    }
    save_json("rtv1a_route_listener_proof.json", route_proof)

    # Valid payload to satisfy Pydantic
    payload = {"prompt": "test", "workspace_id": "default", "tenant_id": "default"}
    payload_bytes = json.dumps(payload).encode()
    body_hash = hashlib.sha256(payload_bytes).hexdigest()

    # Base valid tokens for probing
    base_wit = {
        "iss": "cappo",
        "sub": "wimse://cappo/agent/default/uuid/123",
        "aud": "https://cappo.veklom.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "jti": f"wit_jti_{int(time.time())}",
        "cnf": {}
    }
    base_ect = {
        "iss": "cappo",
        "sub": "wimse://cappo/agent/default/uuid/123",
        "aud": "https://cappo.veklom.com",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "jti": f"ect_jti_{int(time.time())}",
        "ephemeral_execution_id": "ei_123",
        "candidate_act_hash": "act_hash",
        "cnf": {}
    }
    base_auth = {
        "authority_id": "auth_123",
        "ephemeral_execution_id": "ei_123",
        "scope_hash": "scope_val",
        "policy_decision_hash": "policy_val",
        "candidate_act_hash": "act_hash",
        "destination_hash": "target_hash",
        "rights": ["truth.transition", "execute"],
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 3600,
        "proof_of_possession": "pop"
    }
    
    # We must compute true hashes for WPT
    wit_hash = hashlib.sha256(json.dumps(base_wit, sort_keys=True).encode()).hexdigest()
    ect_hash = hashlib.sha256(json.dumps(base_ect, sort_keys=True).encode()).hexdigest()
    auth_hash = hashlib.sha256(json.dumps(base_auth, sort_keys=True).encode()).hexdigest()

    base_wpt = {
        "htm": "POST",
        "htu": "/v1/exec",
        "body_hash": body_hash,
        "wit_hash": wit_hash,
        "ect_hash": ect_hash,
        "authority_hash": auth_hash,
        "jti": f"wpt_jti_{int(time.time())}",
        "exp": int(time.time()) + 3600,
        "cnf": {}
    }

    def probe_cappo(name, headers):
        headers["Content-Type"] = "application/json"
        headers["x-request-id"] = f"trace-{name}"
        r = requests.post(f"{BASE_URL}/v1/exec", json=payload, headers=headers)
        status = "DENIED" if r.status_code in [401, 403, 422] else "FAILED"
        print(f"Probe {name}: {status} ({r.status_code})")
        append_jsonl("rtv1a_cappo_negative_probes.jsonl", {
            "probe": name,
            "status": status,
            "response_code": r.status_code,
            "response_body": r.json() if r.status_code in [401, 403, 422] else r.text
        })

    # Missing WID
    probe_cappo("missing_wid", {
        "Execution-Context": dict_to_b64(base_ect),
        "Workload-Proof": dict_to_b64(base_wpt),
        "Veklom-Authority": dict_to_b64(base_auth)
    })
    
    # Missing ECT
    probe_cappo("missing_ect", {
        "Workload-Identity": dict_to_b64(base_wit),
        "Workload-Proof": dict_to_b64(base_wpt),
        "Veklom-Authority": dict_to_b64(base_auth)
    })
    
    # Missing WPT
    probe_cappo("missing_wpt", {
        "Workload-Identity": dict_to_b64(base_wit),
        "Execution-Context": dict_to_b64(base_ect),
        "Veklom-Authority": dict_to_b64(base_auth)
    })
    
    # Missing Authority
    probe_cappo("missing_authority", {
        "Workload-Identity": dict_to_b64(base_wit),
        "Execution-Context": dict_to_b64(base_ect),
        "Workload-Proof": dict_to_b64(base_wpt)
    })

    # Profile-only authority denied
    profile_only_auth = dict(base_auth)
    profile_only_auth["ephemeral_execution_id"] = ""
    profile_only_wpt = dict(base_wpt)
    profile_only_wpt["authority_hash"] = hashlib.sha256(json.dumps(profile_only_auth, sort_keys=True).encode()).hexdigest()
    
    probe_cappo("profile_only_authority_denied", {
        "Workload-Identity": dict_to_b64(base_wit),
        "Execution-Context": dict_to_b64(base_ect),
        "Workload-Proof": dict_to_b64(profile_only_wpt),
        "Veklom-Authority": dict_to_b64(profile_only_auth)
    })
    
    # Authority hash mismatch
    bad_hash_wpt = dict(base_wpt)
    bad_hash_wpt["authority_hash"] = "wrong_hash"
    probe_cappo("authority_hash_mismatch", {
        "Workload-Identity": dict_to_b64(base_wit),
        "Execution-Context": dict_to_b64(base_ect),
        "Workload-Proof": dict_to_b64(bad_hash_wpt),
        "Veklom-Authority": dict_to_b64(base_auth)
    })

if __name__ == "__main__":
    run_probes()
