import time

import requests

time.sleep(4)
CAPPO_URL = 'http://127.0.0.1:8002'

# 1. Mount
m_resp = requests.post(f'{CAPPO_URL}/v1/capability/mounts', json={
    'package_ref': 'veklom.test@v1',
    'execution_scope': {'workspace': 'public-preview', 'project': 'default'}
})
print('Mount:', m_resp.status_code, m_resp.json().get('decision'))
mount_id = m_resp.json()['mount']['id']
token_id = m_resp.json()['token']['token_id']
nonce = m_resp.json()['token']['nonce']

# 2. Execute
e_resp = requests.post(f'{CAPPO_URL}/v1/capability/mounts/{mount_id}/execute', json={
    'token_id': token_id,
    'nonce': nonce,
    'action': 'record.create',
    'target_ref': 'veklom.test@v1',
    'resource': 'veklom.test@v1',
    'arguments': {'test': True}
})
print('Execute:', e_resp.status_code, e_resp.json().get('decision'), e_resp.json().get('consequence', {}).get('state'))
print('Authority epoch:', e_resp.json().get('authority', {}).get('epoch'))
print('Receipt ID:', e_resp.json().get('consequence', {}).get('receipt_id'))

# 3. Terminate
t_resp = requests.post(f'{CAPPO_URL}/v1/capability/mounts/{mount_id}/terminate', json={
    'reason': 'user_revoked'
})
print('Terminate:', t_resp.status_code, t_resp.json().get('decision'))

# 4. Replay
r_resp = requests.post(f'{CAPPO_URL}/v1/capability/mounts/{mount_id}/execute', json={
    'token_id': token_id,
    'nonce': nonce,
    'action': 'record.create',
    'target_ref': 'veklom.test@v1',
    'resource': 'veklom.test@v1',
    'arguments': {'test': True}
})
print('Replay:', r_resp.status_code, r_resp.text)
