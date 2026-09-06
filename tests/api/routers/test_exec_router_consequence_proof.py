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

def _build_canonical_environment(db: Session, execution_id: str, biscuit_token: str = "dummy", ws_id: str = "test-workspace"):
    import uuid
    mount_id = f"mount-{uuid.uuid4()}"
    token_id = f"token-{uuid.uuid4()}"
    nonce = f"nonce-{uuid.uuid4()}"
    mount = CapabilityMount(
        mount_id=mount_id,
        token_id=token_id,
        token_nonce=nonce,
        owner_principal="auth-disabled",
        owner_workspace=ws_id,
        mount_json={"id": mount_id, "package_ref": "test@v1", "role": "test-role", "scope": {"workspace": ws_id, "project": "test-project"}, "token": {"type": "ephemeral_scoped", "ttl_seconds": 600}, "grants": {"reads": [], "writes": ["execute"], "resources": ["provider-dispatch"], "blocked": [], "external_send": [], "suppression_required": []}, "policy": {"mode": "draft_only", "default": "deny", "require_human_approval_for_external_send": True, "require_suppression_check": True, "persistent_memory_allowed": False}, "lifecycle": {"state": "mounted", "unmount_on": ["task_complete", "token_expiry", "explicit_terminate"]}},
        token_json={**{"biscuit_token": biscuit_token, "token_id": token_id, "mount_id": mount_id, "package_ref": "test@v1", "scope": {"workspace": ws_id, "project": "test-project"}, "grants": {"reads": [], "writes": ["execute"], "resources": ["provider-dispatch"], "blocked": [], "external_send": [], "suppression_required": []}, "policy": {"mode": "draft_only", "default": "deny", "require_human_approval_for_external_send": True, "require_suppression_check": True, "persistent_memory_allowed": False}, "issued_at": 1700000000.0, "expires_at": 1800000000.0, "ttl_seconds": 600, "nonce": nonce, "execution_id": "will-be-replaced"}, "execution_id": execution_id},
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

def test_consequence_dominance_proof_wrong_executor_fails(client: TestClient, db: Session):
    execution_id = str(uuid.uuid4())
    biscuit = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="some-other-executor",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=600
    )
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)
    
    headers = {"X-Workspace-ID": "test-workspace", "Veklom-Authority": biscuit}
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
    assert response.status_code == 403

def test_consequence_dominance_proof_mutated_intent_fails(client: TestClient, db: Session):
    execution_id = str(uuid.uuid4())
    biscuit = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=600
    )
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)
    
    headers = {"X-Workspace-ID": "test-workspace", "Veklom-Authority": biscuit}
    payload = {
        "action": "execute_malicious",
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
    assert response.status_code == 403


def test_consequence_dominance_proof_valid_request(client: TestClient, db: Session):
    execution_id = str(uuid.uuid4())
    valid_biscuit = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=600
    )
    mount = _build_canonical_environment(db, execution_id, biscuit_token=valid_biscuit)
    
    headers = {
        "X-Workspace-ID": "test-workspace",
        "Veklom-Authority": valid_biscuit
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
    assert response.status_code == 200, response.json()


def test_consequence_dominance_proof_expired_mount_fails(client: TestClient, db: Session):
    execution_id = str(uuid.uuid4())
    biscuit = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=600
    )
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)
    
    # Manually expire the mount
    mount.terminated = True
    db.commit()

    headers = {
        "X-Workspace-ID": "test-workspace",
        "Veklom-Authority": biscuit
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
    assert response.status_code == 403
    assert "CAPABILITY_LEASE_NOT_ACTIVE" in response.text

def test_consequence_dominance_proof_persistent_and_ephemeral_invariants(client: TestClient, db: Session):
    # 1. Ephemeral
    exec_id_1 = str(uuid.uuid4())
    biscuit_1 = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=exec_id_1,
        ttl_seconds=600
    )
    mount_1 = _build_canonical_environment(db, exec_id_1, biscuit_token=biscuit_1)
    
    # 2. Persistent
    exec_id_2 = str(uuid.uuid4())
    biscuit_2 = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=exec_id_2,
        ttl_seconds=600
    )
    mount_2 = _build_canonical_environment(db, exec_id_2, biscuit_token=biscuit_2, ws_id="test-workspace-2")
    import copy
    m2_json = copy.deepcopy(mount_2.mount_json)
    m2_json["policy"]["persistent_memory_allowed"] = True
    m2_json["token"]["type"] = "persistent_service"
    mount_2.mount_json = m2_json
    
    t2_json = copy.deepcopy(mount_2.token_json)
    t2_json["single_use"] = False
    t2_json["type"] = "persistent_service"
    mount_2.token_json = t2_json
    db.commit()

    headers_1 = {"X-Workspace-ID": "test-workspace", "Veklom-Authority": biscuit_1}
    payload_1 = {
        "action": "execute",
        "action_cost_cents": 0,
        "capability_lease": {
            "mount_id": mount_1.mount_id,
            "token_id": mount_1.token_id,
            "nonce": mount_1.token_nonce,
            "execution_id": exec_id_1
        },
        "prompt": "Test proof"
    }
    resp_1 = client.post("/v1/exec", json=payload_1, headers=headers_1)
    assert resp_1.status_code == 200

    headers_2 = {"X-Workspace-ID": "test-workspace-2", "Veklom-Authority": biscuit_2}
    payload_2 = {
        "action": "execute",
        "action_cost_cents": 0,
        "capability_lease": {
            "mount_id": mount_2.mount_id,
            "token_id": mount_2.token_id,
            "nonce": mount_2.token_nonce,
            "execution_id": exec_id_2
        },
        "prompt": "Test proof"
    }
    resp_2 = client.post("/v1/exec", json=payload_2, headers=headers_2)
    assert resp_2.status_code == 200, resp_2.text
