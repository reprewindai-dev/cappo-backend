"""Test B — Isolated Proof Test: Requester/Approver Separation.

Demonstrates that an execution identity requesting a high-risk operation
cannot satisfy the independent approval requirement for that same operation,
resulting in a deterministic DENY (SELF_APPROVAL_FORBIDDEN).

Requirements verified:
- R1: Binds original requester identity (canonical agent_id) to high-risk request.
      Rejects any approval where approver_id == requester_id.
      DENIES the self-approval attempt, not the underlying quarantined request.
      Rejected self-approval is NOT added to approvals_received, does NOT advance quorum,
      and does NOT prevent later approvals by distinct valid approvers.
- R2: Preserves existing M-of-N quorum and approver-trust behavior.
- R3: Adversarial test proving:
      requester attempts approval -> deterministic SELF_APPROVAL_FORBIDDEN ->
      quorum unchanged -> request remains pending/quarantined ->
      valid independent approvers still satisfy normal M-of-N behavior.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from cappo_backend.config import Settings
from cappo_backend.main import create_app
from cappo_backend.security.mcp_gateway import MCPGateway
from cappo_backend.services.canonical import sign_payload_hmac
from cappo_backend.services.mcp_v2 import get_mcp_v2_stack, reset_mcp_v2_stack
from cappo_backend.services.safety import (
    AnomalyDetection,
    ApproverTrustError,
    CurrentMetric,
    Observation,
    RequestQuarantineService,
    SelfApprovalForbiddenError,
)


def _critical_anomaly(agent_id: str = "agent-high-risk-001") -> AnomalyDetection:
    return AnomalyDetection(
        detection_id="det-high-risk-1",
        agent_id=agent_id,
        detected_at=datetime.now(timezone.utc),
        anomaly_type="new_capability_access",
        deviation_score=5.5,
        anomaly_score=98.0,
        severity="critical",
        recommended_action="quarantine",
        evidence_hash="sha256-critical-anomaly",
    )


def test_b_requester_cannot_approve_own_request_service_boundary() -> None:
    """Proof Test B (Service Boundary):

    An execution identity requesting a high-risk operation cannot approve
    its own request, yielding a deterministic DENY (SELF_APPROVAL_FORBIDDEN).
    The request remains quarantined, quorum is unchanged, and distinct
    independent approvers can subsequently reach quorum.
    """
    requester_id = "execution-agent-omega"
    required_quorum = 2
    quarantine_service = RequestQuarantineService(approvers_required=required_quorum)

    high_risk_request = {
        "agent_id": requester_id,
        "action": "execute_critical_consequence",
        "parameters": {"target": "production_database", "operation": "DROP_TABLE"},
    }

    # 1. Quarantine high-risk request
    qr = quarantine_service.quarantine(
        high_risk_request,
        [_critical_anomaly(requester_id)],
    )

    # Verify identity binding
    assert qr.requester_id == requester_id
    assert qr.status == "quarantined"
    assert qr.approval_required is True
    assert qr.approvers_required == required_quorum
    assert qr.approvals_received == []

    # 2. Adversarial Action: Requester attempts self-approval
    captured_denial: dict[str, Any] = {}
    with pytest.raises(SelfApprovalForbiddenError) as exc_info:
        quarantine_service.approve(
            qr.quarantine_id,
            approver_id=requester_id,
            approver_trust=100.0,  # Max trust score cannot override separation
        )

    # Verify deterministic rejection
    exc = exc_info.value
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc)
    assert exc.decision == "DENY"
    assert exc.denial_reason == "SELF_APPROVAL_FORBIDDEN"
    assert exc.requester_id == requester_id
    assert exc.approver_id == requester_id

    # 3. Capture evidence of DENY
    captured_denial = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "SELF_APPROVAL_ATTEMPT_DENIED",
        "quarantine_id": qr.quarantine_id,
        "requester_id": exc.requester_id,
        "approver_id": exc.approver_id,
        "decision": exc.decision,
        "denial_reason": exc.denial_reason,
        "underlying_status": qr.status,
        "approvals_received_count": len(qr.approvals_received),
    }
    print(f"\n[SECURITY_EVIDENCE_DENY] {json.dumps(captured_denial, indent=2)}")

    # 4. Security Invariants: Quorum unchanged, request still quarantined
    assert len(qr.approvals_received) == 0
    assert requester_id not in qr.approvals_received
    assert qr.status == "quarantined"
    assert quarantine_service.get(qr.quarantine_id).status == "quarantined"

    # 5. Independent Approver 1 approves -> quorum not yet met
    approver_1 = "human-operator-alice"
    approved_step_1 = quarantine_service.approve(
        qr.quarantine_id,
        approver_id=approver_1,
        approver_trust=95.0,
    )
    assert approved_step_1 is False
    assert qr.approvals_received == [approver_1]
    assert qr.status == "quarantined"

    # 6. Adversarial Re-attempt: Requester attempts self-approval AGAIN
    with pytest.raises(SelfApprovalForbiddenError):
        quarantine_service.approve(
            qr.quarantine_id,
            approver_id=requester_id,
            approver_trust=99.0,
        )

    # Quorum still holds only approver_1
    assert qr.approvals_received == [approver_1]
    assert qr.status == "quarantined"

    # 7. Independent Approver 2 approves -> M-of-N quorum reached
    approver_2 = "human-operator-bob"
    approved_step_2 = quarantine_service.approve(
        qr.quarantine_id,
        approver_id=approver_2,
        approver_trust=92.0,
    )
    assert approved_step_2 is True
    assert qr.approvals_received == [approver_1, approver_2]
    assert qr.status == "approved"
    assert qr.resolution_reason == "Quorum of approvers reached"


def test_b_requester_cannot_approve_own_request_http_router_boundary() -> None:
    """Proof Test B (HTTP Router Boundary):

    Validates /v1/governance/v2/quarantine/{id}/approve HTTP endpoint:
    Self-approval returns 403 Forbidden with SELF_APPROVAL_FORBIDDEN detail.
    Underlying quarantine item remains quarantined. Subsequent valid
    independent approvers achieve quorum via HTTP API.
    """
    reset_mcp_v2_stack()
    settings = Settings(api_keys="test-key", environment="development")
    client = TestClient(create_app(settings))
    headers = {"X-API-Key": "test-key"}

    stack = get_mcp_v2_stack()
    requester_id = "hostile-workload-agent"

    # Directly quarantine high-risk request on the stack
    qr = stack.quarantine.quarantine(
        {"agent_id": requester_id, "operation": "privileged_transfer"},
        [_critical_anomaly(requester_id)],
    )
    quarantine_id = qr.quarantine_id
    assert quarantine_id is not None

    # Check queue
    queue_resp = client.get("/v1/governance/v2/quarantine", headers=headers)
    assert queue_resp.status_code == 200
    items = queue_resp.json()["items"]
    target_item = next(item for item in items if item["quarantine_id"] == quarantine_id)
    assert target_item["status"] == "quarantined"
    assert target_item["approvals_received"] == []

    # Adversarial HTTP Self-Approval
    self_approve_resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=headers,
        json={"approver_id": requester_id, "approver_trust": 99.0},
    )
    assert self_approve_resp.status_code == 403
    error_detail = self_approve_resp.json()["detail"]
    assert "SELF_APPROVAL_FORBIDDEN" in error_detail
    assert requester_id in error_detail

    evidence_http = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "boundary": "HTTP /v1/governance/v2/quarantine/{id}/approve",
        "status_code": self_approve_resp.status_code,
        "detail": error_detail,
        "decision": "DENY",
        "denial_reason": "SELF_APPROVAL_FORBIDDEN",
    }
    print(f"\n[HTTP_SECURITY_EVIDENCE_DENY] {json.dumps(evidence_http, indent=2)}")

    # Verify queue unchanged
    queue_after = client.get("/v1/governance/v2/quarantine", headers=headers).json()
    item_after = next(i for i in queue_after["items"] if i["quarantine_id"] == quarantine_id)
    assert item_after["status"] == "quarantined"
    assert item_after["approvals_received"] == []

    # Valid independent approver 1
    app1_resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=headers,
        json={"approver_id": "valid-independent-approver-1", "approver_trust": 95.0},
    )
    assert app1_resp.status_code == 200
    assert app1_resp.json()["quorum_reached"] is False
    assert app1_resp.json()["status"] == "quarantined"

    # Valid independent approver 2 -> Quorum reached
    app2_resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=headers,
        json={"approver_id": "valid-independent-approver-2", "approver_trust": 90.0},
    )
    assert app2_resp.status_code == 200
    assert app2_resp.json()["quorum_reached"] is True
    assert app2_resp.json()["status"] == "approved"

    reset_mcp_v2_stack()


def test_b_canonical_identity_field_extraction() -> None:
    """Proof Test B (Identity Extraction Variants):

    Verifies that original requester identity is canonically bound regardless
    of whether it appears as 'agent_id', 'requester_id', or nested
    'execution_identity.agent_id'.
    """
    q = RequestQuarantineService(approvers_required=2)

    # Variant 1: top-level 'agent_id'
    qr1 = q.quarantine({"agent_id": "agent-alpha"}, [])
    assert qr1.requester_id == "agent-alpha"
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr1.quarantine_id, "agent-alpha", approver_trust=95.0)

    # Variant 2: top-level 'requester_id'
    qr2 = q.quarantine({"requester_id": "agent-beta"}, [])
    assert qr2.requester_id == "agent-beta"
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr2.quarantine_id, "agent-beta", approver_trust=95.0)

    # Variant 3: nested 'execution_identity.agent_id'
    qr3 = q.quarantine({"execution_identity": {"agent_id": "agent-gamma"}}, [])
    assert qr3.requester_id == "agent-gamma"
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr3.quarantine_id, "agent-gamma", approver_trust=95.0)


def test_b_mcp_gateway_token_self_approval_rejected() -> None:
    """Proof Test B (MCPGateway Bound Approval Token Boundary):

    Cryptographic approval tokens signed by the same identity that requested
    the execution are rejected with SELF_APPROVAL_FORBIDDEN.
    """
    signing_key = "proof-test-signing-key"
    gateway = MCPGateway(
        settings=Settings(
            environment="test",
            approval_token_signing_key=signing_key,
        )
    )
    gateway.redis_client = None

    requester_agent = "autonomous-agent-77"

    # Build signed approval token where approver is the requester
    payload = {
        "approver_id": requester_agent,
        "capability_id": "cloud.database.delete",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
        "nonce": "req-nonce-proof-b",
        "policy_snapshot_id": "policy-snap-proof-b",
        "request_hash": "req-hash-proof-b",
    }
    signature_body = gateway._approval_token_signature_payload(payload)
    payload["signature"] = sign_payload_hmac(signature_body, signing_key)

    # Validation WITH requester_id == approver_id
    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        payload,
        request_hash="req-hash-proof-b",
        policy_snapshot_id="policy-snap-proof-b",
        capability_id="cloud.database.delete",
        request_nonce="req-nonce-proof-b",
        requester_id=requester_agent,
    )

    assert is_valid is False
    assert approver_id is None
    assert "SELF_APPROVAL_FORBIDDEN" in error

    evidence_token = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "boundary": "MCPGateway._validate_bound_approval_token",
        "requester_id": requester_agent,
        "approver_in_token": payload["approver_id"],
        "is_valid": is_valid,
        "error": error,
        "decision": "DENY",
        "denial_reason": "SELF_APPROVAL_FORBIDDEN",
    }
    print(f"\n[TOKEN_SECURITY_EVIDENCE_DENY] {json.dumps(evidence_token, indent=2)}")

    # Validation WITH distinct requester_id != approver_id succeeds
    is_valid_independent, approver_id_indep, error_indep = gateway._validate_bound_approval_token(
        payload,
        request_hash="req-hash-proof-b",
        policy_snapshot_id="policy-snap-proof-b",
        capability_id="cloud.database.delete",
        request_nonce="req-nonce-proof-b",
        requester_id="different-independent-agent",
    )
    assert is_valid_independent is True
    assert approver_id_indep == requester_agent
    assert error_indep == "Valid"


def test_b_predator_authoritative_identity_binding_rejects_spoofed_approver_id() -> None:
    """Predator Test: Authoritative Identity Binding Prevents Body Spoofing.

    Exact scenario specified in user requirement amendment:
    authenticated requester = exec-A
    stored requester         = exec-A

    approval body claims:
    approver_id = exec-B

    authenticated approver identity = exec-A

    EXPECTED:
    DENY / SELF_APPROVAL_FORBIDDEN
    quorum unchanged
    """
    quarantine_service = RequestQuarantineService(approvers_required=2)
    requester_id = "exec-A"
    stored_requester = "exec-A"

    qr = quarantine_service.quarantine(
        {"action": "high_risk_drop_db"},
        [_critical_anomaly(requester_id)],
        requester_id=stored_requester,
    )
    assert qr.requester_id == "exec-A"
    assert qr.status == "quarantined"
    assert qr.approvals_received == []

    # Adversary attempt: Body claims 'exec-B', but authenticated identity is 'exec-A'
    with pytest.raises(SelfApprovalForbiddenError) as exc_info:
        quarantine_service.approve(
            qr.quarantine_id,
            approver_id="exec-B",  # spoofed body claim
            approver_trust=99.0,
            authenticated_approver_id="exec-A",  # real authenticated identity
        )

    exc = exc_info.value
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc)
    assert exc.decision == "DENY"
    assert exc.denial_reason == "SELF_APPROVAL_FORBIDDEN"
    assert exc.requester_id == "exec-A"

    evidence_predator = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test": "PREDATOR_AUTHORITATIVE_IDENTITY_BINDING",
        "stored_requester": qr.requester_id,
        "claimed_approver_id": "exec-B",
        "authenticated_approver_id": "exec-A",
        "decision": exc.decision,
        "denial_reason": exc.denial_reason,
        "approvals_received": qr.approvals_received,
        "status": qr.status,
    }
    print(f"\n[PREDATOR_EVIDENCE_DENY] {json.dumps(evidence_predator, indent=2)}")

    # Invariants: Quorum unchanged, request still quarantined
    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"

    # Distinct valid approver with authenticated identity 'exec-B' succeeds
    valid_step_1 = quarantine_service.approve(
        qr.quarantine_id,
        approver_id="exec-B",
        approver_trust=95.0,
        authenticated_approver_id="exec-B",
    )
    assert valid_step_1 is False
    assert qr.approvals_received == ["exec-B"]
    assert qr.status == "quarantined"


def test_b_predator_http_router_rejects_spoofed_approver_id() -> None:
    """Predator Test (HTTP Router Boundary):

    Validates that supplying a spoofed 'approver_id' in HTTP body cannot
    circumvent self-approval when the authenticated caller identity matches
    the original requester.
    """
    reset_mcp_v2_stack()
    settings = Settings(api_keys="test-key", environment="development")
    client = TestClient(create_app(settings))

    stack = get_mcp_v2_stack()
    requester_id = "exec-A"

    qr = stack.quarantine.quarantine(
        {"operation": "nuclear_delete"},
        [_critical_anomaly(requester_id)],
        requester_id=requester_id,
    )
    quarantine_id = qr.quarantine_id

    # Caller authenticated as 'exec-A' sends body with approver_id='exec-B'
    spoofed_headers = {
        "X-API-Key": "test-key",
        "X-Authenticated-Agent-Id": "exec-A",
    }
    resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=spoofed_headers,
        json={"approver_id": "exec-B", "approver_trust": 95.0},
    )
    assert resp.status_code == 403
    assert "SELF_APPROVAL_FORBIDDEN" in resp.json()["detail"]

    # Verify quorum unchanged via HTTP queue
    queue_resp = client.get("/v1/governance/v2/quarantine", headers=spoofed_headers)
    target = next(i for i in queue_resp.json()["items"] if i["quarantine_id"] == quarantine_id)
    assert target["status"] == "quarantined"
    assert target["approvals_received"] == []
    assert target["requester_id"] == "exec-A"

    reset_mcp_v2_stack()


def test_b_facade_pre_execution_assessment_binds_requester_and_forbids_self_approval() -> None:
    """Proof Test B (Facade Boundary):

    Validates that pre_execution_assessment binds agent_id even when the request
    dictionary omits any 'agent_id' key, preventing self-approval bypass.
    """
    reset_mcp_v2_stack()
    stack = get_mcp_v2_stack()
    attacker = "spiker-workload-99"

    # Seed baseline to produce critical anomaly on spike
    pattern = [9, 10, 11]
    for i in range(60):
        stack.baselines.record_observation(
            attacker,
            Observation(
                timestamp=datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0),
                requests_in_window=pattern[i % 3],
                failure_rate=0.05,
                capabilities_used=("exec",),
            ),
        )
    stack.baselines.build_baseline(attacker)

    # Request payload DOES NOT include agent_id
    evidence = stack.pre_execution_assessment(
        attacker,
        {"operation": "drop_production_tables"},
        metric=CurrentMetric(requests_per_hour=900, failure_rate=0.05, time_of_day=12),
        trust_score=70,
    )
    quarantine_id = evidence["safety"]["quarantine_id"]
    assert quarantine_id is not None

    qr = stack.quarantine.get(quarantine_id)
    assert qr.requester_id == attacker

    # Attacker attempts self-approval -> deterministic DENY
    with pytest.raises(SelfApprovalForbiddenError) as exc_info:
        stack.quarantine.approve(quarantine_id, approver_id=attacker, approver_trust=95.0)

    assert "SELF_APPROVAL_FORBIDDEN" in str(exc_info.value)
    assert exc_info.value.decision == "DENY"
    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"

    reset_mcp_v2_stack()


def test_b_casing_and_whitespace_spoofing_rejected() -> None:
    """Proof Test B (Casing & Whitespace Normalization Boundary):

    Rejects approval attempts where approver varies casing or wraps
    requester identity in whitespace. Also rejects empty/blank approvers.
    """
    q = RequestQuarantineService(approvers_required=2)
    requester = "Hostile-Agent-007"
    qr = q.quarantine({"agent_id": requester}, [_critical_anomaly(requester)])

    # Uppercase variation
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id="HOSTILE-AGENT-007", approver_trust=95.0)

    # Lowercase variation
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id="hostile-agent-007", approver_trust=95.0)

    # Padded with whitespace
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id="  Hostile-Agent-007  ", approver_trust=95.0)

    # Empty string or blank rejected
    with pytest.raises(ValueError):
        q.approve(qr.quarantine_id, approver_id="", approver_trust=95.0)

    with pytest.raises(ValueError):
        q.approve(qr.quarantine_id, approver_id="   ", approver_trust=95.0)

    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"


def test_b_canonical_execution_identity_v1_subject_binding() -> None:
    """Proof Test B (ExecutionIdentityV1 Subject Binding):

    Validates that standard ExecutionIdentityV1 structures with 'subject'
    correctly bind requester_id and deny self-approval.
    """
    q = RequestQuarantineService(approvers_required=2)
    subject = "crypto-workload-alpha"
    ei_payload = {
        "execution_identity": {
            "ei_id": "ei-12345",
            "subject": subject,
            "tenant_id": "production",
            "capabilities": ["database.mutate"],
        }
    }
    qr = q.quarantine(ei_payload, [_critical_anomaly(subject)])
    assert qr.requester_id == subject

    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id=subject, approver_trust=95.0)

    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"


def test_b_concurrent_multithreaded_approval_race() -> None:
    """Proof Test B (Concurrency & Race Condition Boundary):

    Under concurrent multithreaded load with simultaneous self-approval
    attempts and valid independent approvals, zero self-approvals succeed,
    quorum is accurately maintained, and request transitions to approved.
    """
    import threading

    q = RequestQuarantineService(approvers_required=2)
    requester = "race-hostile-agent"
    qr = q.quarantine({"agent_id": requester}, [_critical_anomaly(requester)])

    self_denials: list[str] = []
    approvals: list[str] = []

    def worker(approver: str) -> None:
        try:
            res = q.approve(qr.quarantine_id, approver_id=approver, approver_trust=95.0)
            if res:
                approvals.append(approver)
        except SelfApprovalForbiddenError:
            self_denials.append(approver)
        except Exception:
            pass

    threads = []
    # 10 threads trying self-approval
    for _ in range(10):
        threads.append(threading.Thread(target=worker, args=(requester,)))
    # 5 threads trying valid approver 1
    for _ in range(5):
        threads.append(threading.Thread(target=worker, args=("independent-approver-1",)))
    # 5 threads trying valid approver 2
    for _ in range(5):
        threads.append(threading.Thread(target=worker, args=("independent-approver-2",)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariants:
    # 1. No self-approval was ever added to approvals_received
    assert requester not in qr.approvals_received
    # 2. All 10 self-approval attempts were denied
    assert len(self_denials) == 10
    # 3. Only the 2 valid independent approvers are recorded
    assert set(qr.approvals_received) == {"independent-approver-1", "independent-approver-2"}
    # 4. Quorum is exactly reached
    assert qr.status == "approved"


def test_b_predator_delegation_chain_subagent_and_root_denied_self_approval() -> None:
    """Predator Test (Delegation Chain Boundary):

    Validates that neither a sub-agent executing a delegated action nor the root
    principal that delegated it can approve the quarantined request.
    """
    q = RequestQuarantineService(approvers_required=2)
    root_principal = "corp-root-agent"
    worker_delegate = "worker-subagent"

    request = {
        "action": "execute_high_risk_payout",
        "execution_identity": {
            "subject": worker_delegate,
            "delegation": {
                "root_principal": root_principal,
                "parent_agent_id": root_principal,
                "delegate": worker_delegate,
            },
        },
    }

    qr = q.quarantine(request, [_critical_anomaly(worker_delegate)])
    assert qr.status == "quarantined"
    assert qr.requester_id == worker_delegate
    assert root_principal in qr.bound_identities

    # 1. Sub-agent delegate attempts self-approval -> DENY
    with pytest.raises(SelfApprovalForbiddenError) as exc_1:
        q.approve(qr.quarantine_id, approver_id=worker_delegate, approver_trust=95.0)
    assert exc_1.value.decision == "DENY"
    assert exc_1.value.denial_reason == "SELF_APPROVAL_FORBIDDEN"

    # 2. Root principal that delegated the action attempts approval -> DENY
    with pytest.raises(SelfApprovalForbiddenError) as exc_2:
        q.approve(qr.quarantine_id, approver_id=root_principal, approver_trust=95.0)
    assert exc_2.value.decision == "DENY"
    assert exc_2.value.denial_reason == "SELF_APPROVAL_FORBIDDEN"

    # Invariants: 0 approvals recorded, status still quarantined
    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"

    # 3. Independent third-party approver 1 approves
    assert q.approve(qr.quarantine_id, approver_id="independent-auditor-1", approver_trust=90.0) is False
    assert qr.approvals_received == ["independent-auditor-1"]

    # 4. Independent third-party approver 2 approves -> quorum reached
    assert q.approve(qr.quarantine_id, approver_id="independent-auditor-2", approver_trust=92.0) is True
    assert qr.status == "approved"


def test_b_predator_unicode_and_zero_width_space_spoofing_denied() -> None:
    """Predator Test (Unicode Normalization & Zero-Width Obfuscation):

    Validates that zero-width spaces, byte-order marks, and NFKC fullwidth
    variations cannot evade Requester/Approver separation.
    """
    q = RequestQuarantineService(approvers_required=2)
    requester = "agent-alpha"
    qr = q.quarantine({"agent_id": requester}, [_critical_anomaly(requester)])

    # Zero-width space obfuscation (\u200b)
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id="agent-alpha\u200b", approver_trust=95.0)

    # Byte order mark obfuscation (\ufeff)
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id="\ufeffagent-alpha", approver_trust=95.0)

    # Fullwidth Unicode characters (NFKC)
    fullwidth_requester = "\uff41\uff47\uff45\uff4e\uff54\uff0d\uff41\uff4c\uff50\uff48\uff41"  # "agent-alpha"
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id=fullwidth_requester, approver_trust=95.0)

    # Invariants: 0 approvals recorded, status quarantined
    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"


def test_b_predator_token_claim_spoofing_in_gateway() -> None:
    """Predator Test (Gateway Token Claim Spoofing Boundary):

    Validates that a cryptographic approval token containing approver_id='independent-operator'
    is rejected when its underlying subject matches the requester.
    """
    signing_key = "proof-test-signing-key-spoof"
    gateway = MCPGateway(
        settings=Settings(
            environment="test",
            approval_token_signing_key=signing_key,
        )
    )
    gateway.redis_client = None
    requester_agent = "autonomous-agent-77"

    # Attacker crafts token claiming approver_id='independent-operator' but subject='autonomous-agent-77'
    payload = {
        "approver_id": "independent-operator",
        "subject": requester_agent,  # Real token identity matches requester
        "capability_id": "cloud.database.delete",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
        "nonce": "req-nonce-spoof",
        "policy_snapshot_id": "policy-snap-spoof",
        "request_hash": "req-hash-spoof",
    }
    signature_body = gateway._approval_token_signature_payload(payload)
    payload["signature"] = sign_payload_hmac(signature_body, signing_key)

    is_valid, approver_id, error = gateway._validate_bound_approval_token(
        payload,
        request_hash="req-hash-spoof",
        policy_snapshot_id="policy-snap-spoof",
        capability_id="cloud.database.delete",
        request_nonce="req-nonce-spoof",
        requester_id=requester_agent,
    )

    assert is_valid is False
    assert approver_id is None
    assert "SELF_APPROVAL_FORBIDDEN" in error


def test_b_safety_layer_service_boundary_case_deduplication_and_separation() -> None:
    """Proof Test B (Safety Layer Service Boundary):

    Directly exercises cappo_backend.services.safety_layer.RequestQuarantineService
    to verify that the gateway's twin safety layer enforces:
    1. Deterministic rejection of self-approval (SELF_APPROVAL_FORBIDDEN)
    2. Case-insensitive deduplication preventing M-of-N quorum bypass
    3. Normal M-of-N quorum completion by distinct independent approvers
    """
    from cappo_backend.services.safety_layer import (
        AnomalyDetection as SLAnomalyDetection,
        AnomalyType as SLAnomalyType,
        QuarantineStatus,
        RecommendedAction,
        RequestQuarantineService as SLRequestQuarantineService,
        Severity as SLSeverity,
    )

    sl_quarantine = SLRequestQuarantineService()
    requester = "sl-hostile-agent"

    anomaly = SLAnomalyDetection(
        detection_id="sl-det-1",
        agent_id=requester,
        detected_at=datetime.now(timezone.utc).isoformat(),
        anomaly_type=SLAnomalyType.REQUEST_SPIKE,
        baseline={
            "agent_id": requester,
            "observation_window_days": 7,
            "avg_requests_per_hour": 10.0,
            "std_dev_requests_per_hour": 1.0,
            "avg_failure_rate": 0.01,
            "std_dev_failure_rate": 0.005,
            "typical_capabilities": {"exec": 1},
            "typical_time_windows": [12],
            "typical_error_types": {},
            "confidence_score": 95.0,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "is_locked": True,
        },
        current_metric={
            "requests_per_hour": 1000.0,
            "failure_rate": 0.5,
            "new_capabilities": ["drop"],
            "time_of_day": 12,
            "requests_in_window": 100,
        },
        deviation_score=9.9,
        anomaly_score=99.0,
        severity=SLSeverity.CRITICAL,
        recommended_action=RecommendedAction.QUARANTINE,
        evidence_hash="sl-hash-1",
    )

    qr = sl_quarantine.quarantine(
        {"agent_id": requester, "action": "nuclear_wipe"},
        [anomaly],
    )
    assert qr.status == QuarantineStatus.QUARANTINED
    assert qr.requester_id == requester

    # 1. Requester attempts self-approval -> DENY
    with pytest.raises(SelfApprovalForbiddenError) as exc_info:
        sl_quarantine.approve(qr.quarantine_id, approver_id=requester, approver_trust=95.0)
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc_info.value)
    assert len(qr.approvals_received) == 0
    assert qr.status == QuarantineStatus.QUARANTINED

    # 2. Valid Approver 1 approves
    approved_1 = sl_quarantine.approve(qr.quarantine_id, approver_id="approver-alice", approver_trust=90.0)
    assert approved_1 is False
    assert qr.approvals_received == ["approver-alice"]

    # 3. Case-spoofed attempt with same approver in uppercase ("APPROVER-ALICE") -> deduplicated, does NOT advance quorum
    approved_dup = sl_quarantine.approve(qr.quarantine_id, approver_id="APPROVER-ALICE", approver_trust=90.0)
    assert approved_dup is False
    assert qr.approvals_received == ["approver-alice"]
    assert qr.status == QuarantineStatus.QUARANTINED

    # 4. Distinct independent approver 2 approves -> quorum reached
    approved_2 = sl_quarantine.approve(qr.quarantine_id, approver_id="approver-bob", approver_trust=90.0)
    assert approved_2 is True
    assert qr.approvals_received == ["approver-alice", "approver-bob"]
    assert qr.status == QuarantineStatus.APPROVED


def test_b_predator_jwt_authenticated_caller_denied_body_spoofing_over_http() -> None:
    """Predator Test (HTTP Router Boundary with JWT Token Scope):

    Validates that an authenticated caller with JWT subject 'exec-A' cannot
    bypass self-approval by submitting a forged 'approver_id': 'exec-B' in the HTTP body.
    """
    reset_mcp_v2_stack()
    settings = Settings(api_keys="test-key", environment="development")
    app = create_app(settings)

    # Middleware simulating upstream JWT validation placing claims in request.scope
    @app.middleware("http")
    async def fake_jwt_middleware(request: Request, call_next):
        if request.headers.get("X-Simulate-JWT") == "exec-A":
            request.scope["jwt_payload"] = {"sub": "exec-A", "iss": "auth.veklom.internal"}
            request.scope["auth_principal"] = "jwt:auth.veklom.internal:exec-A"
        return await call_next(request)

    client = TestClient(app)
    stack = get_mcp_v2_stack()
    requester_id = "exec-A"

    qr = stack.quarantine.quarantine(
        {"operation": "privileged_action"},
        [_critical_anomaly(requester_id)],
        requester_id=requester_id,
    )
    quarantine_id = qr.quarantine_id

    # Caller authenticated via JWT as 'exec-A' claims approver_id='exec-B' in body
    headers = {
        "X-API-Key": "test-key",
        "X-Simulate-JWT": "exec-A",
    }
    resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=headers,
        json={"approver_id": "exec-B", "approver_trust": 95.0},
    )
    assert resp.status_code == 403
    assert "SELF_APPROVAL_FORBIDDEN" in resp.json()["detail"]

    # Queue verifies quorum is unchanged (0 approvals) and status still quarantined
    queue_resp = client.get("/v1/governance/v2/quarantine", headers=headers)
    target = next(i for i in queue_resp.json()["items"] if i["quarantine_id"] == quarantine_id)
    assert target["status"] == "quarantined"
    assert target["approvals_received"] == []

    reset_mcp_v2_stack()


def test_b_predator_url_percent_encoding_obfuscation_denied() -> None:
    """Predator Test: URL Percent-Encoding & Multi-Layer Decoding Obfuscation.

    Validates that single and double percent-encoded representations of the requester
    identity (e.g. '%65%78%65%63%2D%41', '%2565%2578%2565%2563%252D%2541', 'exec%2dA')
    are recursively unquoted and deterministically denied with SELF_APPROVAL_FORBIDDEN,
    preventing self-approval or quorum deduplication bypass.
    """
    q = RequestQuarantineService(approvers_required=2)
    requester = "exec-A"
    qr = q.quarantine({"agent_id": requester}, [_critical_anomaly(requester)])

    # Single URL-encoded 'exec-A' -> '%65%78%65%63%2D%41'
    with pytest.raises(SelfApprovalForbiddenError) as exc_1:
        q.approve(qr.quarantine_id, approver_id="%65%78%65%63%2D%41", approver_trust=95.0)
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc_1.value)

    # Partial URL-encoded 'exec-A' -> 'exec%2dA'
    with pytest.raises(SelfApprovalForbiddenError) as exc_2:
        q.approve(qr.quarantine_id, approver_id="exec%2dA", approver_trust=95.0)
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc_2.value)

    # Double URL-encoded 'exec-A' -> '%2565%2578%2565%2563%252D%2541'
    with pytest.raises(SelfApprovalForbiddenError) as exc_3:
        q.approve(qr.quarantine_id, approver_id="%2565%2578%2565%2563%252D%2541", approver_trust=95.0)
    assert "SELF_APPROVAL_FORBIDDEN" in str(exc_3.value)

    # Invariants
    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"

    # Distinct valid approver succeeds
    assert q.approve(qr.quarantine_id, approver_id="independent-approver-1", approver_trust=90.0) is False
    assert qr.approvals_received == ["independent-approver-1"]


def test_b_predator_spiffe_and_urn_identity_scheme_separation() -> None:
    """Predator Test: SPIFFE URI and URN Scheme Boundary Separation.

    Validates that:
    1. A bare requester 'exec-A' cannot be approved by 'spiffe://cluster.local/ns/prod/sa/exec-A'
    2. A bare requester 'exec-A' cannot be approved by 'urn:veklom:agent:exec-A'
    3. A bare requester 'exec-A' cannot be approved by 'exec-A@veklom.internal'
    4. A SPIFFE requester 'spiffe://cluster.local/ns/prod/sa/exec-A' cannot be approved by bare 'exec-A'
    """
    q = RequestQuarantineService(approvers_required=2)

    # Case A: Bare requester vs URI/URN approvers
    qr_bare = q.quarantine({"agent_id": "exec-A"}, [_critical_anomaly("exec-A")])
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr_bare.quarantine_id, approver_id="spiffe://cluster.local/ns/prod/sa/exec-A", approver_trust=95.0)
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr_bare.quarantine_id, approver_id="urn:veklom:agent:exec-A", approver_trust=95.0)
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr_bare.quarantine_id, approver_id="exec-A@veklom.internal", approver_trust=95.0)
    assert len(qr_bare.approvals_received) == 0
    assert qr_bare.status == "quarantined"

    # Case B: SPIFFE requester vs Bare approver
    spiffe_id = "spiffe://cluster.local/ns/prod/sa/exec-A"
    qr_spiffe = q.quarantine({"agent_id": spiffe_id}, [_critical_anomaly(spiffe_id)])
    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr_spiffe.quarantine_id, approver_id="exec-A", approver_trust=95.0)
    assert len(qr_spiffe.approvals_received) == 0
    assert qr_spiffe.status == "quarantined"


def test_b_predator_untrusted_client_header_x_agent_id_cannot_spoof_authenticated_approver() -> None:
    """Predator Test: Untrusted X-Agent-Id client header is ignored.

    A client attempting to approve high-risk request cannot supply X-Agent-Id: exec-B
    to masquerade as an authenticated approver when their body contains their real requester identity.
    """
    reset_mcp_v2_stack()
    settings = Settings(api_keys="test-key", environment="development")
    client = TestClient(create_app(settings))
    stack = get_mcp_v2_stack()
    requester_id = "exec-A"

    qr = stack.quarantine.quarantine(
        {"operation": "nuclear_delete"},
        [_critical_anomaly(requester_id)],
        requester_id=requester_id,
    )
    quarantine_id = qr.quarantine_id

    # Caller passes X-Agent-Id: exec-B to spoof identity, but body has approver_id: exec-A
    headers = {
        "X-API-Key": "test-key",
        "X-Agent-Id": "exec-B",  # Spoofed client header MUST be ignored
    }
    resp = client.post(
        f"/v1/governance/v2/quarantine/{quarantine_id}/approve",
        headers=headers,
        json={"approver_id": requester_id, "approver_trust": 95.0},
    )
    assert resp.status_code == 403
    assert "SELF_APPROVAL_FORBIDDEN" in resp.json()["detail"]

    # Invariants
    queue_resp = client.get("/v1/governance/v2/quarantine", headers=headers)
    target = next(i for i in queue_resp.json()["items"] if i["quarantine_id"] == quarantine_id)
    assert target["status"] == "quarantined"
    assert target["approvals_received"] == []

    reset_mcp_v2_stack()


def test_b_predator_pydantic_model_request_identity_binding() -> None:
    """Predator Test: Pydantic Model Request Identity Extraction.

    Validates that passing a Pydantic model request to quarantine() extracts
    canonical agent_id and denies self-approval.
    """
    from pydantic import BaseModel

    class ExecutionPayload(BaseModel):
        agent_id: str
        action: str
        parameters: dict[str, str]

    q = RequestQuarantineService(approvers_required=2)
    requester = "pydantic-agent-42"
    payload = ExecutionPayload(
        agent_id=requester,
        action="database.drop",
        parameters={"table": "users"},
    )

    qr = q.quarantine(payload, [_critical_anomaly(requester)])
    assert qr.requester_id == requester
    assert requester in qr.bound_identities

    with pytest.raises(SelfApprovalForbiddenError):
        q.approve(qr.quarantine_id, approver_id=requester, approver_trust=95.0)

    assert len(qr.approvals_received) == 0
    assert qr.status == "quarantined"


@pytest.mark.anyio
async def test_b_predator_gateway_process_request_binds_verified_agent_id() -> None:
    """Predator Test: MCPGateway.process_request binds verified agent_id to quarantine in Phase 4."""
    from cappo_backend.core.governance.compliance_profiles import ComplianceProfile, ComplianceRegion
    from cappo_backend.models.mcpapi_v2 import BehavioralBaseline

    gateway = MCPGateway(
        settings=Settings(environment="test", approval_token_signing_key="key"),
        audit_service=None,
    )
    gateway.compliance_profile = ComplianceProfile(
        id="test-profile",
        region=ComplianceRegion.US,
        requires_explicit_evidence_logging=False,
        requires_data_residency=False,
        strict_retention_days=30,
        allowed_model_regions=["US"],
        description="Test profile",
    )
    gateway.redis_client = None

    agent = "gateway-workload-1"
    # Seed baseline with observations to trigger anomaly on spike
    baseline = BehavioralBaseline(
        agent_id=agent,
        observation_window_days=7,
        avg_requests_per_hour=5.0,
        std_dev_requests_per_hour=0.5,
        avg_failure_rate=0.01,
        std_dev_failure_rate=0.005,
        typical_capabilities={"exec": 1},
        typical_time_windows=[12],
        typical_error_types={},
        confidence_score=95.0,
        last_updated=datetime.now(timezone.utc).isoformat(),
        is_locked=True,
    )
    gateway.baseline_service.baselines[agent] = baseline
    gateway.permissions_calculator.calculate_effective_permissions = lambda *args, **kwargs: {"can_execute": True}

    # High spike request triggering CRITICAL request_spike anomaly
    # The payload omits 'agent_id' inside the nested payload to test authoritative binding
    request_data = {
        "connection_id": "conn-test-1",
        "agent_id": agent,
        "capability_id": "exec",
        "nonce": "nonce-gateway-test",
        "payload": {"action": "drop_everything"},
    }

    resp = await gateway.process_request(request_data)
    assert resp.get("status") == "quarantined"
    quarantine_id = resp.get("quarantine_id")
    assert quarantine_id is not None

    qr = gateway.quarantine_service.get_quarantined(quarantine_id)
    assert qr is not None
    assert qr.requester_id == agent

    # Self-approval by gateway agent is denied
    with pytest.raises(SelfApprovalForbiddenError):
        gateway.quarantine_service.approve(quarantine_id, approver_id=agent, approver_trust=95.0)

    assert len(qr.approvals_received) == 0


