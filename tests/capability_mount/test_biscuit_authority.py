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

def test_biscuit_issuance_rejects_caller_supplied_legacy_executor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cappo_backend.api.routers.capability_mount_router
    import cappo_backend.security.biscuit

    minted: dict[str, str | None] = {}
    original_mint = cappo_backend.security.biscuit.mint_biscuit_capability

    def capture_mint(**kwargs):
        minted.update(kwargs)
        return original_mint(**kwargs)

    monkeypatch.setattr(
        cappo_backend.security.biscuit,
        "mint_biscuit_capability",
        capture_mint,
    )

    def mock_verified_caller(req, requested_workspace=None):
        return ("verified-caller", "w1")

    monkeypatch.setattr(
        cappo_backend.api.routers.capability_mount_router,
        "_caller",
        mock_verified_caller,
    )
    prepare(client)

    response = client.post(
        "/v1/capability/mounts",
        json={**mount_payload(60), "executor_spiffe_id": "legacy-unbound"},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "allow"
    assert minted["caller_spiffe_id"]
    assert minted["executor_spiffe_id"] == minted["caller_spiffe_id"]
    assert "legacy-unbound" not in minted.values()

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

def test_biscuit_authority_caller_a_used_by_caller_b(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import cappo_backend.api.routers.capability_mount_router
    
    # Caller A creates a mount
    def mock_caller_a(req, requested_workspace=None):
        return ("caller-a", "w1")
    monkeypatch.setattr(cappo_backend.api.routers.capability_mount_router, "_caller", mock_caller_a)
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    # Caller B attempts to use it
    def mock_caller_b(req, requested_workspace=None):
        return ("caller-b", "w1")
    monkeypatch.setattr(cappo_backend.api.routers.capability_mount_router, "_caller", mock_caller_b)
    response = client.post(
        f'/v1/capability/mounts/{mount_id}/actions',
        json={
            'token_id': token_id,
            'nonce': nonce,
            'action': 'contact.read'
        }
    )
    assert response.status_code == 200
    assert response.json()['decision'] == 'deny'
    assert response.json()['reason'] == 'owner_mismatch'

def test_biscuit_authority_wrong_subject(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    from cappo_backend.security.biscuit import mint_biscuit_capability
    import json
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='vlink://different-machine',
        executor_spiffe_id='test:principal',
        capability_id='outreach@v1',
        reads=['contact.read'],
        writes=[],
        execution_id=body['token']['execution_id'],
        ttl_seconds=60,
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

def test_biscuit_authority_wrong_executor(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    from cappo_backend.security.biscuit import mint_biscuit_capability
    import json
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='test:principal',
        executor_spiffe_id='wrong-executor',
        capability_id='outreach@v1',
        reads=['contact.read'],
        writes=[],
        execution_id=body['token']['execution_id'],
        ttl_seconds=60,
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

def test_biscuit_authority_legacy_unbound_impossible(client: TestClient, db: Session) -> None:
    body = get_mount_with_token(client)
    mount_id = body['mount']['id']
    token_id = body['token']['token_id']
    nonce = body['token']['nonce']
    
    from cappo_backend.security.biscuit import mint_biscuit_capability
    import json
    # Explicitly minting one manually to show that if it WAS somehow minted,
    # the runtime enforcement rejects it because it doesn't match the caller identity.
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='legacy-unbound',
        executor_spiffe_id='legacy-unbound',
        capability_id='outreach@v1',
        reads=['contact.read'],
        writes=[],
        execution_id=body['token']['execution_id'],
        ttl_seconds=60,
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

def test_biscuit_authority_explicit_revocation(client: TestClient, db: Session) -> None:
    from cappo_backend.security.biscuit import verify_biscuit_capability, TrustedRevocationState
    from cappo_backend.security.biscuit import mint_biscuit_capability
    
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='test:caller',
        executor_spiffe_id='test:executor',
        capability_id='test@v1',
        reads=['read'],
        writes=[],
        execution_id='exec-123',
        ttl_seconds=60,
    )
    
    trusted_state = TrustedRevocationState()
    trusted_state.revoke_execution('exec-123')
    
    result = verify_biscuit_capability(
        biscuit_token,
        executor_spiffe_id='test:executor',
        action='read',
        subject_spiffe_id='test:caller',
        trusted_state=trusted_state
    )
    assert result is False

def test_biscuit_authority_stale_epoch(client: TestClient, db: Session) -> None:
    from cappo_backend.security.biscuit import verify_biscuit_capability, TrustedRevocationState
    from cappo_backend.security.biscuit import mint_biscuit_capability
    
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='test:caller',
        executor_spiffe_id='test:executor',
        capability_id='test@v1',
        reads=['read'],
        writes=[],
        execution_id='exec-123',
        ttl_seconds=60,
        revocation_scope='workspace:w1',
        revocation_epoch=5
    )
    
    trusted_state = TrustedRevocationState()
    trusted_state.sync_epochs({'workspace:w1': 10}) # require epoch 10
    
    result = verify_biscuit_capability(
        biscuit_token,
        executor_spiffe_id='test:executor',
        action='read',
        subject_spiffe_id='test:caller',
        trusted_state=trusted_state
    )
    assert result is False

def test_biscuit_authority_delegation_depth(client: TestClient, db: Session) -> None:
    from cappo_backend.security.biscuit import verify_biscuit_capability
    from cappo_backend.security.biscuit import mint_biscuit_capability, attenuate_biscuit_capability
    
    biscuit_token = mint_biscuit_capability(
        caller_spiffe_id='test:caller',
        executor_spiffe_id='test:executor',
        capability_id='test@v1',
        reads=['read'],
        writes=[],
        execution_id='exec-123',
        ttl_seconds=60,
    )
    
    token_1 = attenuate_biscuit_capability(biscuit_token, reads=['read'])
    token_2 = attenuate_biscuit_capability(token_1, reads=['read'])
    
    result = verify_biscuit_capability(
        token_2,
        executor_spiffe_id='test:executor',
        action='read',
        subject_spiffe_id='test:caller'
    )
    assert result is False
