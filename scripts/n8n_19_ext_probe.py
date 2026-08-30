import httpx
import uuid
import datetime
import json
import time
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
    print("N8N-19-EXT TEST - INDEPENDENT EXTERNAL ORIGIN PROOF")
    print("====================================================")

    execution_id = 'exec-ext-' + uuid.uuid4().hex[:10]
    print(f"[{ts()}] execution_id = {execution_id}")

    TARGET_PATH = Path(__file__).resolve().parents[1] / 'scratch' / 'n8n17' / 'n8n_governed_append.jsonl'
    resource = SandboxFileAppendConnector.canonicalize_resource(TARGET_PATH)
    
    KMS_KEYS_FILE = Path.home() / '.cappo_mock_kms_keys.json'
    keys = json.loads(KMS_KEYS_FILE.read_text())
    kid, hex_bytes = list(keys.items())[1]
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_bytes))
    print(f"[{ts()}] KMS key: {kid}")

    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    claims = {
        'iss': 'cappo.veklom.com', 'aud': 'sandbox_file_append',
        'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex,
        'sub': 'workspace:ws_local_n8n16_certification', 'lease_id': 'lease-ext',
        'execution_id': execution_id, 'workflow_id': 'W6r3rV1OqR',
        'allowed_actions': ['fs:append'],
        'allowed_resources': [resource],
        'budget': {'currency': 'USD_CENT', 'max': 1}
    }
    token = pyjwt.encode(claims, private_key, algorithm='EdDSA', headers={'kid': kid})

    def get_count():
        if not TARGET_PATH.exists(): return 0
        return sum(1 for line in TARGET_PATH.read_text().splitlines() if execution_id in line)
    
    print(f"[{ts()}] Initial consequence count = {get_count()}")

    print(f"\n[{ts()}] STEP 1 - Firing payload via Cloudflare Edge Worker (External Origin)...")
    payload = {'veklom_authority': token, 'data': {'action': 'fs:append', 'content': f'N8N-19-EXT EXTERNAL PROOF {execution_id}'}}
    
    try:
        res = httpx.post('https://ext-probe-worker.jh85m8vjq2.workers.dev', json=payload, timeout=10)
        data = res.json()
        print(f"[{ts()}] SUCCESS via External Edge Worker")
        print(f"[{ts()}] Edge Response HTTP {res.status_code}")
        print(f"[{ts()}] Target HTTP {data.get('status')} {data.get('text', '')[:100]}")
        if data.get('status') != 200:
            print("FAILED: Target returned non-200")
            sys.exit(1)
    except Exception as e:
        print(f"FAILED to route request: {e}")
        sys.exit(1)
        
    print(f"\n[{ts()}] STEP 2 - Verify local consequence count")
    time.sleep(2)
    c = get_count()
    if c == 1:
        print(f"[{ts()}] PASS consequence_count = {c}")
    else:
        print(f"FAILED consequence_count = {c}")
        sys.exit(1)
        
    print("\n====================================================")
    print("N8N-19-EXT PROOF COMPLETED.")
    print("====================================================")

if __name__ == '__main__':
    main()
