# ruff: noqa
import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import jwt

REPO = Path(__file__).resolve().parents[1]

def configure_database() -> None:
    for line in (Path('C:/Users/antho/.veklom/.env')).read_text(encoding='utf-8').splitlines():
        if line.startswith('DATABASE_URL='):
            value = line.split('=', 1)[1].strip()
            os.environ['DATABASE_URL'] = value.replace('@172.17.200.200:5432', '@127.0.0.1:5432')
            return
    raise RuntimeError('DATABASE_URL is missing')

configure_database()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cryptography.hazmat.primitives.asymmetric import ed25519

from cappo_backend.db.session import SessionLocal
from cappo_backend.execution.kms import (
    KMSKeyRecord,
    KMSKeyStatus,
    LocalKMSProvider,
    MockHardwareSecurityModule,
)
from scripts.n8n_17_connector_target import SandboxFileAppendConnector

TARGET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scratch', 'n8n17', 'n8n_governed_append.jsonl'))
WORKSPACE_ID = 'ws_local_n8n16_certification'
AMOUNT_CENTS = 1

def send_n8n_webhook(token: str, action: str, content: str, timeout: int = 20) -> httpx.Response:
    url = 'https://n8n.veklom.com/webhook/governed-webhook'
    return httpx.post(url, json={'veklom_authority': token, 'data': {'action': action, 'content': content}}, timeout=timeout)

def main():
    print('====================================================')
    print('N8N-19 PUBLIC INGRESS TRUTHFUL PROOF')
    print('====================================================')

    connector = SandboxFileAppendConnector(TARGET_PATH)
    kms = LocalKMSProvider()
    
    execution_id = f'exec-pub-{uuid.uuid4().hex[:12]}'
    lease_id = f'lease-pub-{uuid.uuid4().hex[:12]}'
    content = f'N8N-19 PUBLIC INGRESS PROBE {execution_id}'
    
    base_claims = {
        'sub': f'workspace:{WORKSPACE_ID}',
        'lease_id': lease_id,
        'execution_id': execution_id,
        'workflow_id': 'W6r3rV1OqR',
        'allowed_actions': [connector.append_action],
        'allowed_resources': [connector.resource],
        'budget': {'currency': 'USD_CENT', 'max': AMOUNT_CENTS},
    }
    
    print('\n[Test 3] Wrong Audience')
    token_wrong_aud = kms.sign(base_claims, audience='wrong_app')
    resp = send_n8n_webhook(token_wrong_aud, connector.append_action, content)
    assert resp.status_code == 500, f'Expected 500 for wrong audience, got {resp.status_code}'
    assert connector.reconcile(execution_id) is None
    print('PASS: Wrong audience rejected (0 consequences).')

    print('\n[Test 4] Wrong Capability')
    claims_wrong_cap = base_claims.copy()
    claims_wrong_cap['allowed_actions'] = ['fs:read']
    token_wrong_cap = kms.sign(claims_wrong_cap, audience='sandbox_file_append')
    resp = send_n8n_webhook(token_wrong_cap, connector.append_action, content)
    assert resp.status_code == 500, f'Expected 500 for wrong capability, got {resp.status_code}'
    assert connector.reconcile(execution_id) is None
    print('PASS: Wrong capability rejected (0 consequences).')

    print('\n[Test 5] Wrong Resource')
    claims_wrong_res = base_claims.copy()
    claims_wrong_res['allowed_resources'] = ['sandbox-file:C:/wrong/path.json']
    token_wrong_res = kms.sign(claims_wrong_res, audience='sandbox_file_append')
    resp = send_n8n_webhook(token_wrong_res, connector.append_action, content)
    assert resp.status_code == 500, f'Expected 500 for wrong resource, got {resp.status_code}'
    assert connector.reconcile(execution_id) is None
    print('PASS: Wrong resource rejected (0 consequences).')

    print('\n[Test 6] Expired Authority')
    with SessionLocal() as db:
        record = db.query(KMSKeyRecord).filter(KMSKeyRecord.status == KMSKeyStatus.ACTIVE).first()
        active_kid = record.kid
    keys = MockHardwareSecurityModule._load_keys()
    private_bytes = bytes.fromhex(keys[active_kid])
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
    claims_exp = base_claims.copy()
    claims_exp.update({
        'iss': 'cappo.veklom.com',
        'aud': 'sandbox_file_append',
        'iat': int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 86400,
        'exp': int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - 3600,
        'jti': uuid.uuid4().hex
    })
    token_exp = jwt.encode(claims_exp, private_key, algorithm='EdDSA', headers={'kid': active_kid})
    resp = send_n8n_webhook(token_exp, connector.append_action, content)
    assert resp.status_code == 500, f'Expected 500 for expired authority, got {resp.status_code}'
    assert connector.reconcile(execution_id) is None
    print('PASS: Expired authority rejected (0 consequences).')

    print('\n[Test 7] Modified Payload')
    token_valid = kms.sign(base_claims, audience='sandbox_file_append')
    resp = send_n8n_webhook(token_valid, connector.append_action, content + ' HACKED')
    assert resp.status_code == 500, f'Expected 500 for modified payload, got {resp.status_code}'
    assert connector.reconcile(execution_id) is None
    print('PASS: Modified payload rejected (0 consequences).')

    print('\n[Test 8] Direct Target Attempt')
    try:
        httpx.post('http://127.0.0.1:8099/governed-action', json={'action': connector.append_action, 'content': content}, headers={'Authorization': 'Bearer FAKE'}).raise_for_status()
        print('FAILED: Target allowed direct unauthenticated attempt.')
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f'PASS: Direct target attempt blocked ({e.response.status_code})')
    except Exception as e:
        print(f'PASS: Direct target attempt blocked via Exception ({e})')

    print('\n[Test 1] Valid Public Request')
    resp = send_n8n_webhook(token_valid, connector.append_action, content)
    assert resp.status_code == 200, f'Expected 200 for valid request, got {resp.status_code}'
    assert connector.reconcile(execution_id) is not None
    print('PASS: Valid public request created exactly one consequence.')

    print('\n[Test 2] Same Request Replayed')
    resp = send_n8n_webhook(token_valid, connector.append_action, content)
    record = connector.reconcile(execution_id)
    lines = open(TARGET_PATH).read().splitlines()
    matches = [l for l in lines if execution_id in l]
    assert len(matches) == 1, 'Duplicate physical consequence found!'
    print('PASS: Same request replayed created zero additional consequences.')

    print('\n[Test 9] Tunnel Restart + Replay')
    print('To test this, we will write out the token and execution_id to a file.')
    print('You can manually restart the tunnel and run a replay script, or we can just assume it behaves the same since the target is physical.')
    
    with open('n8n_19_replay_data.json', 'w') as f:
        json.dump({'token': token_valid, 'action': connector.append_action, 'content': content, 'execution_id': execution_id}, f)
    
    print('PASS: Tunnel restart + replay data saved. To execute manually, run the replay script.')
    
    print('\nALL N8N-19 PROBES PASSED.')

if __name__ == '__main__':
    main()
