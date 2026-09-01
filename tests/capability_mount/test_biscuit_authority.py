from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from cappo_backend.capability_mount.models import CapabilityPackage
from tests.capability_mount.test_api import prepare, mount_payload

def get_mount_with_token(client: TestClient) -> dict:
    anchor = prepare(client)
    response = client.post('/v1/capability/mounts', json=mount_payload(60))
    assert response.status_code == 200
    return response.json()

def test_biscuit_authority_valid(client: TestClient) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'allow'

def test_biscuit_authority_missing(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    db.execute(text("""
        UPDATE capability_mounts 
        SET token_json = json_set(token_json, '$.biscuit_token', 'null') 
        WHERE token_id = :id
    """), {'id': token_id})
    db.commit()
        
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'missing_cryptographic_authority'

def test_biscuit_authority_malformed(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    db.execute(text("""
        UPDATE capability_mounts 
        SET token_json = json_set(token_json, '$.biscuit_token', '"malformed_base64_garbage"') 
        WHERE token_id = :id
    """), {'id': token_id})
    db.commit()
        
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'missing_cryptographic_authority'

def test_biscuit_authority_expired(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    from cappo_backend.security.biscuit import mint_biscuit_capability
    import json
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='legacy-unbound',
        executor_spiffe_id='legacy-unbound',
        capability_id='outreach@v1',
        reads=['contact.read'],
        writes=[],
        execution_id=body['token']['execution_id'],
        ttl_seconds=-10,
        revocation_scope=f"execution:{body['token']['execution_id']}",
        revocation_epoch=0,
    )
    
    db.execute(text("""
        UPDATE capability_mounts 
        SET token_json = json_set(token_json, '$.biscuit_token', :b_tok) 
        WHERE token_id = :id
    """), {'id': token_id, 'b_tok': f'"{biscuit_token}"'})
    db.commit()
        
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'missing_cryptographic_authority'

def test_biscuit_authority_wrong_root(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from biscuit_auth import KeyPair
    new_kp = KeyPair()
    import cappo_backend.security.biscuit
    monkeypatch.setattr(cappo_backend.security.biscuit, 'get_root_key_pair', lambda: new_kp)
    
    anchor = prepare(client)
    response = client.post('/v1/capability/mounts', json=mount_payload(60))
    assert response.status_code == 200
    body = response.json()
    
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    monkeypatch.undo()
    
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'missing_cryptographic_authority'

def test_biscuit_authority_wrong_workspace(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    from cappo_backend.security.biscuit import mint_biscuit_capability
    import json
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='legacy-unbound',
        executor_spiffe_id='legacy-unbound',
        capability_id='outreach@v1',
        reads=['contact.read'],
        writes=[],
        execution_id=body['token']['execution_id'],
        ttl_seconds=60,
        revocation_scope="execution:wrong-workspace-id",
        revocation_epoch=0,
    )
    
    db.execute(text("""
        UPDATE capability_mounts 
        SET token_json = json_set(token_json, '$.biscuit_token', :b_tok) 
        WHERE token_id = :id
    """), {'id': token_id, 'b_tok': f'"{biscuit_token}"'})
    db.commit()
        
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'missing_cryptographic_authority'

def test_biscuit_authority_replay(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    # First execution succeeds
    response = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response.status_code == 200
    assert response.json()['decision'] == 'allow'
    
    # Second execution with same nonce fails
    response2 = client.post(f'/v1/capability/mounts/{mount_id}/actions', json={
        'token_id': token_id,
        'nonce': nonce,
        'action': 'contact.read'
    })
    assert response2.status_code == 200
    assert response2.json()['decision'] == 'deny'
    assert response2.json()['reason'] == 'token_replay'
