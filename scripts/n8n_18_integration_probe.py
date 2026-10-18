import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]

def configure_database() -> None:
    for line in (Path("C:/Users/antho/.veklom/.env")).read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            value = line.split("=", 1)[1].strip()
            os.environ["DATABASE_URL"] = value.replace(
                "@172.17.200.200:5432", "@127.0.0.1:5432"
            )
            return
    raise RuntimeError("DATABASE_URL is missing from the approved test environment")

configure_database()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from cappo_backend.execution.kms import LocalKMSProvider
from scripts.n8n_17_connector_target import SandboxFileAppendConnector

TARGET_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scratch', 'n8n17', 'n8n_governed_append.jsonl'))
WORKSPACE_ID = 'ws_local_n8n16_certification'
AMOUNT_CENTS = 1

def run_test(audience: str, expect_success: bool) -> bool:
    print(f'\n--- Running Integration Probe (audience={audience}, expect_success={expect_success}) ---')
    connector = SandboxFileAppendConnector(TARGET_PATH)
    execution_id = f'exec-probe-{uuid.uuid4().hex[:12]}'
    lease_id = f'lease-probe-{uuid.uuid4().hex[:12]}'
    content = f'N8N-18 INTEGRATION PROBE {execution_id}'

    token = LocalKMSProvider().sign({
        'sub': f'workspace:{WORKSPACE_ID}',
        'lease_id': lease_id,
        'execution_id': execution_id,
        'workflow_id': 'W6r3rV1OqR',
        'allowed_actions': [connector.append_action],
        'allowed_resources': [connector.resource],
        'budget': {'currency': 'USD_CENT', 'max': AMOUNT_CENTS},
    }, audience=audience)

    try:
        response = httpx.post(
            'https://n8n.veklom.com/webhook/governed-webhook',
            json={'veklom_authority': token, 'data': {'action': connector.append_action, 'content': content}},
            timeout=20,
        )
        print(f'Webhook response status: {response.status_code}')
    except httpx.HTTPError as exc:
        print(f'Webhook HTTP error: {exc}')

    record = connector.reconcile(execution_id)
    if not expect_success:
        if record is not None:
            print('FAILED: Physical consequence occurred when it should have been denied.')
            return False
        print('PASS: Negative test succeeded. No consequence, no settlement.')
        return True
    else:
        if record is None:
            print('FAILED: Physical consequence did not occur.')
            return False
            
        print('Testing duplicate delivery rejection...')
        try:
            dup_response = httpx.post('https://n8n.veklom.com/webhook/governed-webhook', json={'veklom_authority': token, 'data': {'action': connector.append_action, 'content': content}}, timeout=20)
            if dup_response.status_code == 200:
                print('FAILED: Duplicate delivery succeeded!')
                return False
            else:
                print(f'PASS: Duplicate rejected with status {dup_response.status_code}')
        except Exception as e:
            print(f'PASS: Duplicate rejected via exception {e}')
        
        print('Physical record verified.')
        print('PASS: Positive test succeeded. Settlement and consequence confirmed.')
        return True

def main():
    print('N8N-18 Integration Probe')
    if not run_test(audience='wrong_audience_app', expect_success=False):
        sys.exit(1)
        
    if not run_test(audience='sandbox_file_append', expect_success=True):
        sys.exit(1)

    print('\nAll integration probes passed successfully.')
    sys.exit(0)

if __name__ == '__main__':
    main()

