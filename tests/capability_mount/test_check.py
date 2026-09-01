import pytest

def test_check_biscuit(client):
    from tests.capability_mount.test_api import prepare, mount_payload
    prepare(client)
    response = client.post('/v1/capability/mounts', json=mount_payload(9999))
    body = response.json()
    print('RESPONSE KEYS:', body['token'].keys())
    print('BISCUIT_TOKEN:', body['token'].get('biscuit_token'))
