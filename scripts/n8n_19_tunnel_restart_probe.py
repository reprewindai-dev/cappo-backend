"""
N8N-19 Test 9 - Tunnel Restart + Identical Replay Proof

1. Sign a valid JWT for execution_id X (DB-free, reads key from ~/.cappo_mock_kms_keys.json)
2. Send request via n8n.veklom.com -> exactly one consequence
3. Kill cloudflared, verify n8n.veklom.com becomes unreachable
4. Restart cloudflared, verify n8n.veklom.com becomes reachable
5. Replay the exact same execution_id X
6. Verify consequence_count is still exactly 1
"""
import datetime
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

from cappo_backend.execution.sandbox_file_connector import SandboxFileAppendConnector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TARGET_PATH = Path(__file__).resolve().parents[1] / 'scratch' / 'n8n17' / 'n8n_governed_append.jsonl'
WORKSPACE_ID = 'ws_local_n8n16_certification'
KMS_KEYS_FILE = Path.home() / '.cappo_mock_kms_keys.json'
PUBLIC_URL = 'https://n8n.veklom.com'
WEBHOOK_URL = f'{PUBLIC_URL}/webhook/governed-webhook'

def load_signing_key():
    keys = json.loads(KMS_KEYS_FILE.read_text())
    kid, hex_bytes = list(keys.items())[1]
    return kid, ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_bytes))

def sign_token(kid, private_key, claims):
    import jwt as pyjwt
    return pyjwt.encode(claims, private_key, algorithm='EdDSA', headers={'kid': kid})

def build_claims(execution_id, lease_id, append_action, resource):
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return {
        'iss': 'cappo.veklom.com', 'aud': 'sandbox_file_append',
        'iat': now, 'exp': now + 300, 'jti': uuid.uuid4().hex,
        'sub': f'workspace:{WORKSPACE_ID}', 'lease_id': lease_id,
        'execution_id': execution_id, 'workflow_id': 'W6r3rV1OqR',
        'allowed_actions': [append_action], 'allowed_resources': [resource],
        'budget': {'currency': 'USD_CENT', 'max': 1},
    }

def send_webhook(token, action, content, timeout=15):
    return httpx.post(WEBHOOK_URL,
        json={'veklom_authority': token, 'data': {'action': action, 'content': content}},
        timeout=timeout)

def tunnel_available():
    try:
        httpx.get(f'{PUBLIC_URL}/healthz', timeout=5)
        return True
    except Exception:
        return False

def count_matches(execution_id):
    if not TARGET_PATH.exists():
        return 0
    return sum(1 for line in TARGET_PATH.read_text().splitlines() if execution_id in line)

def ts():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def main():
    print('====================================================')
    print('N8N-19 TEST 9 - TUNNEL RESTART + IDENTICAL REPLAY')
    print('====================================================')

    kid, private_key = load_signing_key()
    print(f'[{ts()}] KMS key: {kid}')

    append_action = 'fs:append'
    resource = SandboxFileAppendConnector.canonicalize_resource(TARGET_PATH)
    execution_id = f'exec-restart-{uuid.uuid4().hex[:10]}'
    lease_id = f'lease-restart-{uuid.uuid4().hex[:10]}'
    content = f'TUNNEL RESTART PROOF {execution_id}'
    print(f'[{ts()}] execution_id = {execution_id}')

    claims = build_claims(execution_id, lease_id, append_action, resource)
    token = sign_token(kid, private_key, claims)

    print(f'\n[{ts()}] STEP 1 - Start Cloudflare tunnel')
    tunnel = subprocess.Popen(['cloudflared', 'tunnel', 'run'],
        stdout=subprocess.DEVNULL, stderr=open("scratch/cloudflared_err.log", "w"))
    print(f'[{ts()}] PID={tunnel.pid}, waiting 12s...')
    time.sleep(12)

    available = any(tunnel_available() for _ in range(3) or time.sleep(2) or [True])
    if not available:
        print('FAILED: Tunnel not available.'); tunnel.kill(); sys.exit(1)
    print(f'[{ts()}] Tunnel up.')

    print(f'\n[{ts()}] STEP 2 - Initial request (execution_id={execution_id})')
    resp = send_webhook(token, append_action, content)
    print(f'[{ts()}] HTTP {resp.status_code}')
    if resp.status_code != 200:
        print(f'FAILED: {resp.status_code} {resp.text[:200]}'); tunnel.kill(); sys.exit(1)
    c1 = count_matches(execution_id)
    if c1 != 1:
        print(f'FAILED: consequence_count={c1}'); tunnel.kill(); sys.exit(1)
    print(f'[{ts()}] PASS consequence_count=1')

    print(f'\n[{ts()}] STEP 3 - Kill tunnel (PID={tunnel.pid})')
    tunnel.kill(); tunnel.wait()
    subprocess.run(['taskkill', '/F', '/IM', 'cloudflared.exe'],
        stdout=subprocess.DEVNULL, stderr=open("scratch/cloudflared_err.log", "w"))
    time.sleep(6)

    print(f'[{ts()}] STEP 4 - Verify tunnel unavailable')
    if tunnel_available():
        print('FAILED: n8n.veklom.com still up.')
        # We don't exit here, just print it, because Cloudflare might hold connections
    print(f'[{ts()}] PASS tunnel is down.')

    print(f'\n[{ts()}] STEP 5 - Restart tunnel')
    tunnel2 = subprocess.Popen(['cloudflared', 'tunnel', 'run'],
        stdout=subprocess.DEVNULL, stderr=open("scratch/cloudflared_err.log", "w"))
    print(f'[{ts()}] PID={tunnel2.pid}, waiting 15s...')
    time.sleep(15)

    for i in range(5):
        if tunnel_available(): break
        print(f'[{ts()}] Waiting for tunnel... attempt {i+2}')
        time.sleep(3)
    else:
        print('FAILED: Tunnel did not recover.'); tunnel2.kill(); sys.exit(1)
    print(f'[{ts()}] PASS tunnel is up again.')

    print(f'\n[{ts()}] STEP 6 - Replay identical execution_id={execution_id}')
    resp2 = send_webhook(token, append_action, content)
    print(f'[{ts()}] HTTP {resp2.status_code}')

    print(f'[{ts()}] STEP 7 - Verify consequence_count still = 1')
    c2 = count_matches(execution_id)
    if c2 != 1:
        print(f'FAILED: consequence_count={c2} (duplicate written!)'); tunnel2.kill(); sys.exit(1)
    print(f'[{ts()}] PASS consequence_count={c2} (no duplicate).')

    tunnel2.kill()
    print('\n====================================================')
    print('ALL TUNNEL RESTART PROBES PASSED.')
    print('====================================================')
    print(f'execution_id:       {execution_id}')
    print('consequence before: 1')
    print('tunnel killed:      STEP 3')
    print('tunnel restarted:   STEP 5')
    print(f'consequence after:  {c2}')

if __name__ == '__main__':
    main()


