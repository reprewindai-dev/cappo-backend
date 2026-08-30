import httpx
import uuid
import datetime
import json
import time
import os
import subprocess
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519
import jwt as pyjwt
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cappo_backend.execution.sandbox_file_connector import SandboxFileAppendConnector

def ts():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def main():
    print("====================================================")
    print("G1.2-G1.5 OFFLINE AUTHORITY & REGRESSION PROBE")
    print("====================================================")

    TARGET_PATH = Path(__file__).resolve().parents[1] / 'scratch' / 'n8n17' / 'n8n_governed_append.jsonl'
    resource = SandboxFileAppendConnector.canonicalize_resource(TARGET_PATH)
    
    KMS_KEYS_FILE = Path.home() / '.cappo_mock_kms_keys.json'
    keys = json.loads(KMS_KEYS_FILE.read_text())
    kid, hex_bytes = list(keys.items())[1]
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_bytes))
    print(f"[{ts()}] Loaded KMS Key: {kid}")

    # Generate tokens
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    base_claims = {
        'iss': 'cappo.veklom.com', 'aud': 'sandbox_file_append',
        'sub': 'workspace:ws_offline_test', 'lease_id': 'lease-offline',
        'workflow_id': 'W6r3rV1OqR', 'allowed_actions': ['fs:append'],
        'allowed_resources': [resource], 'budget': {'currency': 'USD_CENT', 'max': 1}
    }

    # Token 0: Valid Initial (to cache the key)
    exec_init = 'exec-init-' + uuid.uuid4().hex[:8]
    c_init = base_claims.copy()
    c_init.update({'execution_id': exec_init, 'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex})
    token_init = pyjwt.encode(c_init, private_key, algorithm='EdDSA', headers={'kid': kid})

    # Token 1: Expired (G1.2)
    exec_expired = 'exec-exp-' + uuid.uuid4().hex[:8]
    c_exp = base_claims.copy()
    c_exp.update({'execution_id': exec_expired, 'iat': now - 600, 'exp': now - 300, 'jti': uuid.uuid4().hex})
    token_expired = pyjwt.encode(c_exp, private_key, algorithm='EdDSA', headers={'kid': kid})

    # Token 2: Wrong Scope (G1.3)
    exec_scope = 'exec-scp-' + uuid.uuid4().hex[:8]
    c_scope = base_claims.copy()
    c_scope.update({'execution_id': exec_scope, 'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex, 'allowed_resources': ['sandbox-file:/wrong/path']})
    token_scope = pyjwt.encode(c_scope, private_key, algorithm='EdDSA', headers={'kid': kid})

    # Token 3: Valid Offline (G1.4 & G1.5)
    exec_valid = 'exec-val-' + uuid.uuid4().hex[:8]
    c_valid = base_claims.copy()
    c_valid.update({'execution_id': exec_valid, 'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex})
    token_valid = pyjwt.encode(c_valid, private_key, algorithm='EdDSA', headers={'kid': kid})


    # STEP 0: Cache the key while online
    print(f"\n[{ts()}] STEP 0: Online pre-flight to cache KMS key...")
    res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': f'INIT CACHE {exec_init}'},
                     headers={'Authorization': f'Bearer {token_init}'})
    print(f"[{ts()}] Init Response: HTTP {res.status_code}")
    if res.status_code != 200: sys.exit("Init failed.")

    # STEP 1: Go Offline
    print(f"\n[{ts()}] STEP 1: GOING OFFLINE (Killing CAPPO Authority on :8002)...")
    try:
        # Kill the uvicorn process running on 8002
        out = subprocess.check_output('netstat -ano | findstr :8002', shell=True).decode()
        for line in out.splitlines():
            if 'LISTENING' in line:
                pid = line.strip().split()[-1]
                subprocess.run(f'taskkill /F /PID {pid}', shell=True)
                print(f"[{ts()}] Killed CAPPO PID {pid}")
    except Exception as e:
        print(f"[{ts()}] Failed to kill CAPPO: {e}")
    time.sleep(2)
    # Verify offline
    try:
        httpx.get('http://127.0.0.1:8002/health')
        print("ERROR: CAPPO is still online!")
    except httpx.ConnectError:
        print(f"[{ts()}] CONFIRMED: CAPPO Authority is Offline.")

    def get_count(eid):
        if not TARGET_PATH.exists(): return 0
        return sum(1 for line in TARGET_PATH.read_text().splitlines() if eid in line)

    # STEP 2: G1.2 Expired Offline Authority
    print(f"\n[{ts()}] STEP 2: G1.2 - Testing EXPIRED token offline...")
    res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': 'EXPIRED'},
                     headers={'Authorization': f'Bearer {token_expired}'})
    print(f"[{ts()}] Expired Response: HTTP {res.status_code} {res.text}")
    if res.status_code == 403:
        print(f"[{ts()}] PASS G1.2: Expired token rejected locally.")
    else: sys.exit("FAIL G1.2")

    # STEP 3: G1.3 Wrong Scope Offline Authority
    print(f"\n[{ts()}] STEP 3: G1.3 - Testing WRONG SCOPE offline...")
    res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': 'SCOPE'},
                     headers={'Authorization': f'Bearer {token_scope}'})
    print(f"[{ts()}] Scope Response: HTTP {res.status_code} {res.text}")
    if res.status_code == 403:
        print(f"[{ts()}] PASS G1.3: Wrong scope rejected locally.")
    else: sys.exit("FAIL G1.3")

    # STEP 4: G1.4 Valid Offline Execution & Evidence
    print(f"\n[{ts()}] STEP 4: G1.4 - Testing VALID token offline (Execution {exec_valid})...")
    res = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                     json={'action': 'fs:append', 'content': f'VALID OFFLINE {exec_valid}'},
                     headers={'Authorization': f'Bearer {token_valid}'})
    print(f"[{ts()}] Valid Response: HTTP {res.status_code}")
    if res.status_code == 200:
        data = res.json()
        print(f"[{ts()}] SUCCESS. Returned evidence locally: {data['receipt_id']}")
        print(f"[{ts()}] PASS G1.4: Offline execution succeeded and generated local evidence.")
    else: sys.exit(f"FAIL G1.4: {res.text}")

    # STEP 5: G1.5 Idempotency / Identical Replay
    print(f"\n[{ts()}] STEP 5: G1.5 - Testing EXACT REPLAY offline...")
    c_before = get_count(exec_valid)
    res2 = httpx.post('http://127.0.0.1:8099/connectors/sandbox-file-append', 
                      json={'action': 'fs:append', 'content': f'VALID OFFLINE {exec_valid}'},
                      headers={'Authorization': f'Bearer {token_valid}'})
    print(f"[{ts()}] Replay Response: HTTP {res2.status_code}")
    c_after = get_count(exec_valid)
    if res2.status_code == 200 and res2.json() == data and c_before == 1 and c_after == 1:
        print(f"[{ts()}] SUCCESS. Exact same evidence returned, physical consequence NOT duplicated.")
        print(f"[{ts()}] PASS G1.5: Idempotency registry protected physical target locally.")
    else: sys.exit("FAIL G1.5")

    print("\n====================================================")
    print("ALL G1 FOUNDATIONAL PROBES COMPLETED SUCCESSFULLY.")
    print("====================================================")

if __name__ == '__main__':
    main()
