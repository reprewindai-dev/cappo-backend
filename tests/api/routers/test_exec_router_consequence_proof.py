import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.models.free_run_quota import FreeRunQuota
from cappo_backend.security.biscuit import mint_biscuit_capability
from cappo_backend.services.activation_target import ACTIVATION_WRITE_ACTION


def _now() -> datetime:
    return datetime.now(timezone.utc)

def _build_canonical_environment(
    db: Session,
    execution_id: str,
    biscuit_token: str = "dummy",
    ws_id: str = "test-workspace",
    *,
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    resources: list[str] | None = None,
    expires_at: datetime | None = None,
    package_ref: str = "test@v1",
):
    import uuid
    reads = reads if reads is not None else []
    writes = writes if writes is not None else ["execute"]
    resources = resources if resources is not None else ["provider-dispatch"]
    mount_id = f"mount-{uuid.uuid4()}"
    token_id = f"token-{uuid.uuid4()}"
    nonce = f"nonce-{uuid.uuid4()}"
    mount_expires_at = expires_at or (_now() + timedelta(days=1))
    grants = {
        "reads": reads,
        "writes": writes,
        "resources": resources,
        "blocked": [],
        "external_send": [],
        "suppression_required": [],
    }
    policy = {
        "mode": "draft_only",
        "default": "deny",
        "require_human_approval_for_external_send": True,
        "require_suppression_check": True,
        "persistent_memory_allowed": False,
    }
    mount = CapabilityMount(
        mount_id=mount_id,
        token_id=token_id,
        token_nonce=nonce,
        owner_principal="auth-disabled",
        owner_workspace=ws_id,
        mount_json={
            "id": mount_id,
            "package_ref": package_ref,
            "role": "test-role",
            "scope": {"workspace": ws_id, "project": "test-project"},
            "token": {"type": "ephemeral_scoped", "ttl_seconds": 600},
            "grants": grants,
            "policy": policy,
            "lifecycle": {
                "state": "mounted",
                "unmount_on": ["task_complete", "token_expiry", "explicit_terminate"],
            },
        },
        token_json={
            "biscuit_token": biscuit_token,
            "token_id": token_id,
            "mount_id": mount_id,
            "package_ref": package_ref,
            "scope": {"workspace": ws_id, "project": "test-project"},
            "grants": grants,
            "policy": policy,
            "issued_at": 1700000000.0,
            "expires_at": 1800000000.0,
            "ttl_seconds": 600,
            "nonce": nonce,
            "execution_id": execution_id,
        },
        issued_at=_now(),
        expires_at=mount_expires_at,
        terminated=False,
    )
    quota = db.get(FreeRunQuota, ws_id)
    if quota is None:
        quota = FreeRunQuota(workspace_id=ws_id, runs_used=0, quota_limit=100)
        db.add(quota)
    else:
        quota.runs_used = 0
        quota.quota_limit = 100
    db.add(mount)
    db.commit()
    return mount


def _mint(
    execution_id: str,
    *,
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    resources: list[str] | None = None,
    caller_spiffe_id: str = "auth-disabled",
    executor_spiffe_id: str | None = "cappo-backend",
    ttl_seconds: int = 600,
    revocation_scope: str = "workspace",
) -> str:
    return mint_biscuit_capability(
        caller_spiffe_id=caller_spiffe_id,
        executor_spiffe_id=executor_spiffe_id,
        capability_id="test@v1",
        reads=reads or [],
        writes=writes or ["execute"],
        resources=resources or ["provider-dispatch"],
        execution_id=execution_id,
        ttl_seconds=ttl_seconds,
        revocation_scope=revocation_scope,
    )


def _payload(mount: CapabilityMount, execution_id: str, **overrides: object) -> dict:
    payload = {
        "action": "execute",
        "action_cost_cents": 0,
        "capability_lease": {
            "mount_id": mount.mount_id,
            "token_id": mount.token_id,
            "nonce": mount.token_nonce,
            "execution_id": execution_id,
        },
        "prompt": "Test proof",
    }
    payload.update(overrides)
    return payload


def _post_exec(
    client: TestClient,
    mount: CapabilityMount,
    execution_id: str,
    biscuit_token: str | None,
    **payload_overrides: object,
):
    headers = {"X-Workspace-ID": mount.owner_workspace}
    if biscuit_token is not None:
        headers["Veklom-Authority"] = biscuit_token
    return client.post(
        "/v1/exec",
        json=_payload(mount, execution_id, **payload_overrides),
        headers=headers,
    )


def _events_for(db: Session, operation_id: str) -> list[ConsequenceExecutionEvent]:
    return db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .order_by(ConsequenceExecutionEvent.version)
    ).scalars().all()


def _events_for_execution(
    db: Session,
    execution_id: str,
) -> list[ConsequenceExecutionEvent]:
    events = _events_for(db, f"exec:{execution_id}")
    events.extend(_events_for(db, execution_id))
    return sorted(events, key=lambda event: (event.operation_id, event.version))

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
    mount = _build_canonical_environment(
        db,
        execution_id,
        biscuit_token=biscuit,
        expires_at=_now() - timedelta(hours=1),
    )

    response = _post_exec(client, mount, execution_id, biscuit)
    assert response.status_code == 403
    assert "CAPABILITY_LEASE_NOT_ACTIVE" in response.text
    assert "expired" in response.text


def test_consequence_dominance_proof_cross_lease_biscuit_fails(
    client: TestClient,
    db: Session,
):
    execution_id_a = str(uuid.uuid4())
    execution_id_b = str(uuid.uuid4())
    biscuit_a = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id_a,
        ttl_seconds=600,
    )
    biscuit_b = mint_biscuit_capability(
        caller_spiffe_id="auth-disabled",
        executor_spiffe_id="cappo-backend",
        capability_id="test@v1",
        reads=[],
        writes=["execute"],
        resources=["provider-dispatch"],
        execution_id=execution_id_b,
        ttl_seconds=600,
    )
    mount = _build_canonical_environment(
        db,
        execution_id_a,
        biscuit_token=biscuit_a,
    )

    response = client.post(
        "/v1/exec",
        json={
            "action": "execute",
            "action_cost_cents": 0,
            "capability_lease": {
                "mount_id": mount.mount_id,
                "token_id": mount.token_id,
                "nonce": mount.token_nonce,
                "execution_id": execution_id_a,
            },
            "prompt": "Cross-lease authority",
        },
        headers={
            "X-Workspace-ID": "test-workspace",
            "Veklom-Authority": biscuit_b,
        },
    )

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text

def test_consequence_dominance_proof_replay_identical_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    first = _post_exec(client, mount, execution_id, biscuit)
    second = _post_exec(client, mount, execution_id, biscuit)

    assert first.status_code == 200, first.text
    assert second.status_code == 403
    assert "CAPABILITY_LEASE_DENIED" in second.text
    assert "token_replay" in second.text


def test_consequence_dominance_proof_replay_with_mutated_prompt_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    first = _post_exec(client, mount, execution_id, biscuit)
    second = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        prompt="Mutated intent after consumption",
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 403
    assert "CAPABILITY_LEASE_DENIED" in second.text
    assert "token_replay" in second.text


def test_consequence_dominance_proof_action_out_of_grants_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        action="delete",
    )

    assert response.status_code == 403
    assert "CAPABILITY_ACTION_OUT_OF_SCOPE" in response.text


def test_consequence_dominance_proof_biscuit_action_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    stored_biscuit = _mint(execution_id)
    header_biscuit = _mint(execution_id, writes=["read"])
    mount = _build_canonical_environment(
        db,
        execution_id,
        biscuit_token=stored_biscuit,
    )

    response = _post_exec(client, mount, execution_id, header_biscuit)

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text


def test_consequence_dominance_proof_biscuit_resource_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    stored_biscuit = _mint(execution_id)
    header_biscuit = _mint(execution_id, resources=["other-resource"])
    mount = _build_canonical_environment(
        db,
        execution_id,
        biscuit_token=stored_biscuit,
    )

    response = _post_exec(client, mount, execution_id, header_biscuit)

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text


def test_consequence_dominance_proof_biscuit_subject_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id, caller_spiffe_id="spiffe://attacker")
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(client, mount, execution_id, biscuit)

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text


def test_consequence_dominance_proof_expired_biscuit_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id, ttl_seconds=-60)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(client, mount, execution_id, biscuit)

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text


def test_consequence_dominance_proof_unknown_revocation_scope_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id, revocation_scope="tenant-unknown")
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(client, mount, execution_id, biscuit)

    assert response.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in response.text


def test_consequence_dominance_proof_token_id_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        capability_lease={
            "mount_id": mount.mount_id,
            "token_id": str(uuid.uuid4()),
            "nonce": mount.token_nonce,
            "execution_id": execution_id,
        },
    )

    assert response.status_code == 403
    assert "CAPABILITY_PROOF_INVALID" in response.text


def test_consequence_dominance_proof_nonce_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        capability_lease={
            "mount_id": mount.mount_id,
            "token_id": mount.token_id,
            "nonce": "wrong-nonce",
            "execution_id": execution_id,
        },
    )

    assert response.status_code == 403
    assert "CAPABILITY_PROOF_INVALID" in response.text


def test_consequence_dominance_proof_execution_id_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        capability_lease={
            "mount_id": mount.mount_id,
            "token_id": mount.token_id,
            "nonce": mount.token_nonce,
            "execution_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 403
    assert "CAPABILITY_EXECUTION_ID_MISMATCH" in response.text


def test_consequence_dominance_proof_unknown_mount_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        capability_lease={
            "mount_id": "mount-does-not-exist",
            "token_id": mount.token_id,
            "nonce": mount.token_nonce,
            "execution_id": execution_id,
        },
    )

    assert response.status_code == 403
    assert "CAPABILITY_LEASE_NOT_ACTIVE" in response.text


def test_consequence_dominance_proof_foreign_workspace_header_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = client.post(
        "/v1/exec",
        json=_payload(mount, execution_id),
        headers={
            "X-Workspace-ID": "intruder-ws",
            "Veklom-Authority": biscuit,
        },
    )

    assert response.status_code == 403
    assert (
        "CAPABILITY_LEASE_NOT_ACTIVE" in response.text
        or "WORKSPACE_SCOPE_MISMATCH" in response.text
    )


def test_consequence_dominance_proof_body_workspace_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        workspace_id="other-ws",
    )

    assert response.status_code == 403
    assert "WORKSPACE_MISMATCH" in response.text


def test_consequence_dominance_proof_mount_without_stored_biscuit_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token="")

    response = _post_exec(client, mount, execution_id, biscuit)

    assert response.status_code == 403
    assert "CRYPTOGRAPHIC_AUTHORITY_REQUIRED" in response.text


def test_consequence_dominance_proof_missing_authority_header_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    first = _post_exec(client, mount, execution_id, None)
    follow_up = _post_exec(client, mount, execution_id, biscuit)

    assert first.status_code == 403
    assert "CONSEQUENCE_DOMINANCE_VIOLATION" in first.text
    assert follow_up.status_code == 403
    assert "CAPABILITY_LEASE_DENIED" in follow_up.text
    assert "token_replay" in follow_up.text


def test_consequence_dominance_proof_activation_reserved_action_package_mismatch_fails(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id, writes=[ACTIVATION_WRITE_ACTION])
    mount = _build_canonical_environment(
        db,
        execution_id,
        biscuit_token=biscuit,
        writes=[ACTIVATION_WRITE_ACTION],
    )

    response = _post_exec(
        client,
        mount,
        execution_id,
        biscuit,
        action=ACTIVATION_WRITE_ACTION,
    )

    assert response.status_code == 403
    assert "ACTIVATION_RESERVED_ACTION_PACKAGE_MISMATCH" in response.text


def test_consequence_dominance_proof_valid_run_leaves_evidence(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    response = _post_exec(client, mount, execution_id, biscuit)

    assert response.status_code == 200, response.text
    receipt = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.token_id == mount.token_id,
            CapabilityActionReceipt.decision == "allow",
        )
    ).scalar_one()
    assert receipt.biscuit_token_sha256 == hashlib.sha256(biscuit.encode()).hexdigest()

    events = _events_for(db, f"exec:{execution_id}")
    assert events
    assert {event.state for event in events} >= {"authorized", "started", "succeeded"}
    assert events[-1].state == "succeeded"


def test_consequence_dominance_proof_persistent_and_ephemeral_share_authority_semantics(
    client: TestClient,
    db: Session,
):
    ephemeral_id = str(uuid.uuid4())
    ephemeral_biscuit = _mint(ephemeral_id)
    ephemeral_mount = _build_canonical_environment(
        db,
        ephemeral_id,
        biscuit_token=ephemeral_biscuit,
    )
    persistent_id = str(uuid.uuid4())
    persistent_biscuit = _mint(persistent_id)
    persistent_mount = _build_canonical_environment(
        db,
        persistent_id,
        biscuit_token=persistent_biscuit,
        ws_id="test-workspace-2",
    )

    ephemeral_response = _post_exec(
        client,
        ephemeral_mount,
        ephemeral_id,
        ephemeral_biscuit,
        execution_mode="ephemeral",
    )
    persistent_response = _post_exec(
        client,
        persistent_mount,
        persistent_id,
        persistent_biscuit,
    )

    assert ephemeral_response.status_code == 200, ephemeral_response.text
    assert persistent_response.status_code == 200, persistent_response.text

    ephemeral_events = _events_for_execution(db, ephemeral_id)
    persistent_events = _events_for(db, f"exec:{persistent_id}")
    ephemeral_states = {event.state for event in ephemeral_events}
    persistent_states = {event.state for event in persistent_events}
    assert persistent_states == {"authorized", "started", "succeeded"}
    assert ephemeral_states == persistent_states
    receipts = db.execute(
        select(CapabilityActionReceipt).where(
            CapabilityActionReceipt.token_id.in_(
                [ephemeral_mount.token_id, persistent_mount.token_id]
            ),
            CapabilityActionReceipt.decision == "allow",
        )
    ).scalars().all()
    assert len(receipts) == 2
    assert not any(event.state == "DISSOLVED" for event in ephemeral_events)
    assert not any(event.state == "DISSOLVED" for event in persistent_events)

    ephemeral_replay = _post_exec(
        client,
        ephemeral_mount,
        ephemeral_id,
        ephemeral_biscuit,
        execution_mode="ephemeral",
    )
    persistent_replay = _post_exec(
        client,
        persistent_mount,
        persistent_id,
        persistent_biscuit,
    )
    assert ephemeral_replay.status_code == 403
    assert persistent_replay.status_code == 403
    assert "token_replay" in ephemeral_replay.text
    assert "token_replay" in persistent_replay.text


def test_consequence_dominance_proof_terminated_after_success_denies_replay(
    client: TestClient,
    db: Session,
):
    execution_id = str(uuid.uuid4())
    biscuit = _mint(execution_id)
    mount = _build_canonical_environment(db, execution_id, biscuit_token=biscuit)

    first = _post_exec(client, mount, execution_id, biscuit)
    mount.terminated = True
    db.commit()
    replay = _post_exec(client, mount, execution_id, biscuit)

    assert first.status_code == 200, first.text
    assert replay.status_code == 403
    assert "CAPABILITY_LEASE_NOT_ACTIVE" in replay.text
    assert "terminated" in replay.text or "token_replay" in replay.text
