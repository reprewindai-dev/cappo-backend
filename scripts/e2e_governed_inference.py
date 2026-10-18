"""End-to-end check of governed inference against a running CAPPO, over HTTP only.

Plays two parts: a signed-in Capability OS user (Blueprint Studio) and the cAPI
gatekeeper that signs requests before they reach CAPPO. For a local test deployment
only: it needs the private halves of CAPPO's JWT and gatekeeper keys, as hex, in a
JSON file ({"jwt_private": ..., "gatekeeper_private": ...}).

    CAPPO_URL=http://127.0.0.1:8002 E2E_WORKSPACE=<id> E2E_MODEL=llama3.2 \
        python scripts/e2e_governed_inference.py keys.json

CAPPO must run with JWT auth (issuer https://lockerphycer.local, audience cappo),
CAPI_EXTERNAL_VALIDATION_ENABLED=true, INFERENCE_ALLOWED_PROVIDERS=ollama, and the
workspace's Ollama registered (docs/governed-inference.md). Each successful run uses
one of the workspace's daily runs.
"""
import base64
import hashlib
import json
import os
import sys
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE = os.environ.get("CAPPO_URL", "http://127.0.0.1:8002")
WORKSPACE = os.environ.get("E2E_WORKSPACE", "ws-blueprint-e2e")
MODEL = os.environ.get("E2E_MODEL", "qwen2.5:0.5b")
AUD = "https://cappo.veklom.com"
keys = json.load(open(sys.argv[1]))
JWT_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys["jwt_private"]))
GATEKEEPER = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys["gatekeeper_private"]))
client = httpx.Client(base_url=BASE, timeout=180, trust_env=False)
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def user_token(workspace=WORKSPACE, sub="anthony"):
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "iss": "https://lockerphycer.local", "aud": "cappo",
         "iat": now, "exp": now + 600, "workspace_id": workspace},
        JWT_KEY, algorithm="EdDSA",
    )


def auth(workspace=WORKSPACE):
    return {"Authorization": f"Bearer {user_token(workspace)}"}


def mount(workspace=WORKSPACE):
    r = client.post("/v1/capability/mounts", headers=auth(workspace), json={
        "package_ref": "veklom.inference@v1",
        "execution_scope": {"workspace": workspace, "project": "blueprint"},
        "requested_action_scope": {"writes": ["llm.exec"]},
        "role": "ephemeral_executor",
        "policy": {"mode": "draft_only"},
        "ttl_seconds": 300,
    })
    body = r.json()
    if r.status_code != 200 or body.get("decision") != "allow":
        raise SystemExit(f"mount failed: {r.status_code} {body}")
    return body


def b64(d):
    return base64.b64encode(json.dumps(d).encode()).decode()


def sha(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def canonical(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def exec_call(m, prompt, *, workspace=WORKSPACE, extra=None, ect_exec_id=None,
              tamper=False, sign=True, identity=True, data_overrides=None):
    tok = m["token"]
    payload = {
        "prompt": prompt, "action": "llm.exec", "directive": "ALLOW",
        "pgl_id": "blueprint-studio", "workspace_id": workspace,
        "capability_lease": {"mount_id": m["mount"]["id"], "token_id": tok["token_id"],
                             "nonce": tok["nonce"], "execution_id": tok["execution_id"]},
        **(extra or {}),
    }
    # cAPI gatekeeper security envelope over actor/action/data-hash/nonce.
    data = {k: v for k, v in payload.items() if k != "capability_lease"}
    defaults = {"agent_id": None, "pgl_id": None, "workspace_id": "default", "tenant_id": "default",
                "delegation_depth": 0, "budget_approved_cents": 0, "action_cost_cents": 0,
                "scope": None, "genome_hash": None, "constitution_hash": None, "plan_hash": None,
                "action": None, "directive": None, "risk_tier": None, "execution_mode": "live"}
    data = {**defaults, **data}
    nonce = str(uuid.uuid4())
    env = {"actor_id": "blueprint-studio", "action": "llm.exec",
           "data_hash": hashlib.sha256(canonical(data).encode()).hexdigest(), "nonce": nonce}
    payload["security"] = {"nonce": nonce, "signature": base64.urlsafe_b64encode(
        GATEKEEPER.sign(canonical(env).encode())).decode().rstrip("=")}
    raw = json.dumps(payload).encode()

    now = int(time.time())
    wit = {"iss": "https://identity.veklom.com", "sub": "wimse://veklom.com/ws/blueprint/studio/run",
           "aud": AUD, "exp": now + 120, "iat": now, "jti": str(uuid.uuid4()), "cnf": {}}
    ect = {**{k: wit[k] for k in ("iss", "sub", "aud", "exp", "iat")}, "jti": str(uuid.uuid4()),
           "ephemeral_execution_id": ect_exec_id or tok["execution_id"],
           "candidate_act_hash": hashlib.sha256(raw).hexdigest(), "cnf": {}}
    wpt = {"htm": "POST", "htu": "/v1/exec", "body_hash": hashlib.sha256(raw).hexdigest(),
           "wit_hash": sha(wit), "ect_hash": sha(ect), "authority_hash": "",
           "jti": str(uuid.uuid4()), "cnf": {}}
    headers = {"Content-Type": "application/json", **auth(workspace)}
    if identity:
        headers.update({"Workload-Identity": b64(wit), "Execution-Context": b64(ect),
                        "Workload-Proof": b64(wpt)})
    if sign:
        digest = "sha-256=:" + base64.b64encode(hashlib.sha256(raw).digest()).decode() + ":"
        covered = {k.lower(): headers[k] for k in ("Workload-Identity", "Execution-Context", "Workload-Proof")}
        fields = ("@method", "@target-uri", "content-digest", *covered)
        quoted = " ".join(f'"{f}"' for f in fields)
        params = f';created={int(time.time())};keyid="capi-gatekeeper"'
        base = ['"@method": POST', f'"@target-uri": {BASE}/v1/exec', f'"content-digest": {digest}',
                *(f'"{k}": {v}' for k, v in covered.items()), f'"@signature-params": ({quoted}){params}']
        sig = base64.b64encode(GATEKEEPER.sign("\n".join(base).encode())).decode()
        headers.update({"Content-Digest": digest, "Signature-Input": f"sig1=({quoted}){params}",
                        "Signature": f"sig1=:{sig}:"})
    if tamper:
        raw = raw.replace(b"Turn these", b"Leak these")
    return client.post("/v1/exec", content=raw, headers=headers)


NOTES = ("Turn these messy notes into a 3-bullet plan: "
         "app for dog walkers, book walks, pay by card, walkers get rated, launch in Austin first.")

print("== 1. capability discovery")
r = client.get("/v1/capability/packages", headers=auth())
data = r.json()
ids = [p.get("id") for p in (data if isinstance(data, list) else data.get("packages", []))]
check("veklom.inference@v1 is offered", "veklom.inference@v1" in ids, f"packages={ids}")

print("== 2. governed AI call (real model through CAPPO)")
m1 = mount()
t0 = time.time()
r = exec_call(m1, NOTES)
body = r.json()
check("POST /v1/exec returns 200", r.status_code == 200, f"{r.status_code} {str(body)[:300] if r.status_code != 200 else ''}")
if r.status_code == 200:
    print(f"   provider={body['provider']} model={body['model']} tokens={body['tokens']} "
          f"latency={time.time()-t0:.1f}s")
    print("   model answer:\n      " + body["response"].strip().replace("\n", "\n      "))
    check("answer came from the local Ollama model", body["provider"] == "ollama" and body["model"] == MODEL)
    check("answer is non-empty", len(body["response"].strip()) > 20)
    check("lease consumed and receipted", (body.get("capability_lease") or {}).get("receipt_id") is not None,
          json.dumps(body.get("capability_lease")))
    check("PGL evidence ids returned", bool(body.get("pgl")), json.dumps(body.get("pgl")))
    
print("== 3. the same lease again (replay)")
r = exec_call(m1, NOTES)
check("replay refused", r.status_code == 403 and "token_replay" in r.text, f"{r.status_code} {r.text[:200]}")

print("== 4. caller tries to add a provider")
r = exec_call(mount(), "Say OK.", extra={"scope": {"tools": ["llm.exec"], "allowed_provider_set": ["openai"]}})
check("provider set cannot be widened (still ollama)", r.status_code == 200 and r.json().get("provider") == "ollama",
      f"{r.status_code} {r.text[:200]}")

print("== 5. body changed after the gatekeeper signed it")
r = exec_call(mount(), NOTES, tamper=True)
check("tampered request refused (401)", r.status_code == 401 and "HTTP_MESSAGE_INTEGRITY_INVALID" in r.text,
      f"{r.status_code} {r.text[:200]}")

print("== 6. no gatekeeper signature")
r = exec_call(mount(), NOTES, sign=False)
check("unsigned request refused", r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}")

print("== 7. no workload identity headers")
r = exec_call(mount(), NOTES, identity=False, sign=False)
check("missing identity refused", r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}")

print("== 8. identity minted for a different execution")
r = exec_call(mount(), NOTES, ect_exec_id=str(uuid.uuid4()))
check("execution-context mismatch refused", r.status_code == 403 and "LEASE_MISMATCH" in r.text,
      f"{r.status_code} {r.text[:200]}")

print("== 9. another workspace uses this workspace's lease")
m9 = mount()
r = exec_call(m9, NOTES, workspace="ws-someone-else")
check("cross-workspace lease refused", r.status_code == 403, f"{r.status_code} {r.text[:200]}")

print("== 10. terminated mount")
m10 = mount()
t = client.post(f"/v1/capability/mounts/{m10['mount']['id']}/terminate", headers=auth(),
                json={"reason": "explicit_terminate"})
r = exec_call(m10, NOTES)
check("terminated mount refused", t.status_code == 200 and r.status_code == 403, f"terminate={t.status_code} exec={r.status_code} {r.text[:200]}")

print("== 11. not signed in")
r = client.post("/v1/exec", json={"prompt": "x"})
check("anonymous call refused (401)", r.status_code == 401, f"{r.status_code} {r.text[:120]}")

passed = sum(ok for _, ok in results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
