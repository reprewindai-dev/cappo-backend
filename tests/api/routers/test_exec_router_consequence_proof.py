import hashlib
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.security.biscuit import mint_biscuit_capability

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _build_canonical_environment(db: Session, execution_id: str, ws_id: str = "test-workspace"):
    mount = CapabilityMount(
        mount_id="mount-abc",
        token_id="token-001",
        token_nonce="nonce-001",
        owner_principal="test:principal",
        owner_workspace=ws_id,
        mount_json={"target_resource": "provider-dispatch", "capability_id": "test@v1"},
        token_json={"execution_id": execution_id, "capabilities": ["test@v1"]},
        issued_at=_now(),
        expires_at=_now() + timedelta(days=1),
        terminated=False,
    )
    db.add(mount)
    db.commit()
    return mount

def test_consequence_dominance_proof_invalid_biscuit_fails(client: TestClient, db: Session):
    execution_id = str(uuid.uuid4())
    mount = _build_canonical_environment(db, execution_id)
    
    # We will sign with a different caller_spiffe_id to simulate an invalid token for this caller.
    garbage_biscuit = mint_biscuit_capability(
        caller_spiffe_id="imposter:principal",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=600
    )
    
    headers = {
        "X-Workspace-ID": "test-workspace",
        "Veklom-Authority": garbage_biscuit
    }
    
    payload = {
        "action": "execute",
        "action_cost_cents": 0,
        "capability_lease": {
            "mount_id": mount.mount_id,
            "token_id": mount.token_id,
            "nonce": mount.token_nonce,
            "execution_id": execution_id
        },
        "prompt": "Test proof"
    }
    
    response = client.post("/v1/exec", json=payload, headers=headers)
    
    # It must fail closed
    assert response.status_code == 403
    
def test_consequence_dominance_proof_direct_target_without_mount_fails(client: TestClient, db: Session):
    # Payload with NO capability_lease
    payload = {
        "action": "execute",
        "action_cost_cents": 0,
        "prompt": "Direct mutation"
    }
    headers = {
        "X-Workspace-ID": "test-workspace",
    }
    response = client.post("/v1/exec", json=payload, headers=headers)
    assert response.status_code == 403
    assert "CAPABILITY_LEASE_REQUIRED" in response.json()["detail"]["error"]
