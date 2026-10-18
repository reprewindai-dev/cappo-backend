"""Adversarial and boundary test suite for Cappy MCP v2 Semantic Adapter.

Proves:
- Test Case 1: Valid MCP session + valid OAuth + CAPPO DENY -> consequence does NOT execute (mock counter = 0).
- Test Case 2: Valid MCP session + valid OAuth + CAPPO ALLOW -> consequence executes (counter = 1) and PGL evidence receipt emitted.
- Test Case 3: Fabricated / replayed session or spent nonce -> deterministic rejection prior to calling CAPPO.
- Invariant: 8-way intersection authority = identity ∩ connection ∩ capability ∩ operation ∩ execution identity ∩ policy ∩ lifecycle ∩ current CAPPO authorization.
- Invariant: MCP session != authority; CAPPO decides.
- Nonce atomic single-use thread safety under concurrent replay attempts.
- Compliance with MCPPort Protocol.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from cappo_backend.models.mcpapi_v2 import (
    AuthorityBundle,
    AuthorityPermissions,
    VeklomAgent,
)
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.governance import Policy, PolicyRule
from cappo_backend.services.mcp_adapter import (
    CapabilityContext,
    ConnectionContext,
    GovernanceDeniedError,
    InvalidSessionError,
    MCPPort,
    MCPv2Adapter,
    PayloadBoundExceededError,
    PerimeterDefenseError,
    ReplayAttackError,
    create_mcp_adapter,
)
from cappo_backend.services.mcp_v2 import MCPv2Stack
from cappo_backend.services.safety import CurrentMetric


class MockConsequenceExecutor:
    """Mock execution engine representing the real consequence substrate."""
    def __init__(self) -> None:
        self.call_count = 0
        self.invocations: list[dict[str, Any]] = []

    def execute(
        self,
        connection_ctx: ConnectionContext,
        capability_ctx: CapabilityContext,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.call_count += 1
        record = {
            "count": self.call_count,
            "connection_id": connection_ctx.connection_id,
            "capability_id": capability_ctx.capability_id,
            "payload": capability_ctx.payload,
        }
        self.invocations.append(record)
        return {"status": "executed", "call_count": self.call_count}


# ============================================================================
# PROTOCOL & FACTORY CONFORMANCE
# ============================================================================

def test_mcp_adapter_satisfies_mcpport_protocol():
    """Verify that MCPv2Adapter conforms structurally to the MCPPort Protocol."""
    adapter = create_mcp_adapter()
    assert isinstance(adapter, MCPPort)
    assert hasattr(adapter, "normalize_request")
    assert hasattr(adapter, "assess_consequence_eligibility")
    assert hasattr(adapter, "dispatch_execution")
    assert hasattr(adapter, "format_response")
    assert hasattr(adapter, "format_error")
    assert hasattr(adapter, "handle_tool_call")


# ============================================================================
# TEST CASE 1: Valid Session + Valid OAuth + CAPPO DENY -> No Execution
# ============================================================================

def test_case_1_valid_session_valid_oauth_cappo_deny_blocks_consequence():
    """Test Case 1: Valid MCP session + valid OAuth + CAPPO DENY -> consequence does NOT execute.

    Proves:
    1. Adapter authenticates and normalizes request.
    2. CAPPO pre-execution assessment returns allow=False.
    3. Consequence executor is NEVER invoked (mock counter remains 0).
    4. No PGL receipt is emitted.
    5. Response contains standard JSON-RPC 2.0 error with CAPPO_GOVERNANCE_DENIED.
    """
    adapter = create_mcp_adapter()
    executor = MockConsequenceExecutor()

    # System policy explicitly denying execution
    deny_policy = Policy(
        policy_id="policy-deny-dangerous-tools",
        rules=[PolicyRule(effect="deny", description="Explicit block on database.drop")],
    )

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-adversarial-01",
        "method": "tools/call",
        "params": {
            "name": "database.drop",
            "arguments": {"target": "production_users"},
            "_meta": {
                "connection_id": "conn-valid-111",
                "session_id": "sess-valid-222",
                "nonce": "nonce-case1-9988776655443322",
                "tenant_id": "tenant-corp",
                "agent_id": "agent-authenticated-01",
            },
        },
    }

    headers = {
        "Authorization": "Bearer eyJhbGciOiJFZERTQSI.valid_oauth_token",
        "Mcp-Session-Id": "sess-valid-222",
        "X-Nonce": "nonce-case1-9988776655443322",
        "X-Agent-ID": "agent-authenticated-01",
    }

    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "session_id": "sess-valid-222",
        "actor_id": "agent-authenticated-01",
    }

    # Execute via adapter
    response = adapter.handle_tool_call(
        raw_request=raw_request,
        headers=headers,
        execution_handler=executor.execute,
        session_auth=session_auth,
        system_policy=deny_policy,
    )

    # Invariant: Consequence MUST NOT execute
    assert executor.call_count == 0
    assert len(executor.invocations) == 0

    # Verify structured rejection
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "req-adversarial-01"
    assert "error" in response
    assert response["error"]["code"] == -32003
    assert response["error"]["message"] == "CAPPO_GOVERNANCE_DENIED"

    error_data = response["error"]["data"]
    assert "evidence_hash" in error_data
    assert error_data["evidence_hash"] is not None
    assert error_data["intersection"]["effective_authority"] is False
    assert error_data["intersection"]["cappo_authorization"] is False


# ============================================================================
# TEST CASE 2: Valid Session + Valid OAuth + CAPPO ALLOW -> Consequence Executes & PGL Emitted
# ============================================================================

def test_case_2_valid_session_valid_oauth_cappo_allow_executes_and_emits_pgl():
    """Test Case 2: Valid MCP session + valid OAuth + CAPPO ALLOW -> consequence executes & PGL receipt emitted.

    Proves:
    1. Adapter authenticates and normalizes request into Veklom contracts.
    2. CAPPO pre-execution assessment returns allow=True.
    3. Consequence executes exactly once (counter increments to 1).
    4. PGL action receipt is minted with Merkle leaf index and canonical content_hash.
    5. Response contains JSON-RPC 2.0 result with evidence_hash and receipt.
    """
    mock_pgl = MagicMock()
    adapter = create_mcp_adapter(pgl_client=mock_pgl)
    executor = MockConsequenceExecutor()

    # System policy explicitly allowing execution
    allow_policy = Policy(
        policy_id="policy-allow-deploy",
        rules=[PolicyRule(effect="allow", description="Allowed worker deploy")],
    )

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-adversarial-02",
        "method": "tools/call",
        "params": {
            "name": "service.deploy",
            "arguments": {
                "service_name": "worker-mesh",
                "replicas": 3,
            },
            "_meta": {
                "connection_id": "conn-valid-333",
                "session_id": "sess-valid-444",
                "nonce": "nonce-case2-1122334455667788",
                "tenant_id": "tenant-corp",
                "agent_id": "agent-authenticated-02",
            },
        },
    }

    headers = {
        "Authorization": "Bearer eyJhbGciOiJFZERTQSI.valid_oauth_token",
        "Mcp-Session-Id": "sess-valid-444",
        "X-Nonce": "nonce-case2-1122334455667788",
        "X-Agent-ID": "agent-authenticated-02",
    }

    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "session_id": "sess-valid-444",
        "actor_id": "agent-authenticated-02",
    }

    response = adapter.handle_tool_call(
        raw_request=raw_request,
        headers=headers,
        execution_handler=executor.execute,
        session_auth=session_auth,
        system_policy=allow_policy,
        trust_score=95.0,
    )

    # Invariant: Consequence executes exactly once
    assert executor.call_count == 1
    assert len(executor.invocations) == 1
    assert executor.invocations[0]["capability_id"] == "service.deploy"

    # Verify JSON-RPC 2.0 success response
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "req-adversarial-02"
    assert "result" in response

    res = response["result"]
    assert res["status"] == "success"
    assert "evidence_hash" in res
    assert len(res["evidence_hash"]) == 64  # SHA-256 hex length

    # Verify PGL Action Receipt
    assert "receipt" in res
    receipt = res["receipt"]
    assert receipt["decision"] == "allow"
    assert receipt["capability_id"] == "service.deploy"
    assert receipt["principal"] == "agent-authenticated-02"
    assert receipt["evidence_hash"] == res["evidence_hash"]
    assert receipt["merkle_leaf_index"] >= 1

    # Verify receipt tamper-evidence content_hash
    recomputed_hash = sha256_json({
        "receipt_id": receipt["receipt_id"],
        "connection_id": receipt["connection_id"],
        "capability_id": receipt["capability_id"],
        "principal": receipt["principal"],
        "action": receipt["action"],
        "decision": "allow",
        "merkle_leaf_index": receipt["merkle_leaf_index"],
        "evidence_hash": receipt["evidence_hash"],
        "actioned_at": receipt["actioned_at"],
    })
    assert receipt["content_hash"] == recomputed_hash

    # Verify PGL Client interaction
    mock_pgl.append_evidence_event.assert_called_once()
    call_kwargs = mock_pgl.append_evidence_event.call_args[1]
    assert call_kwargs["certificate_id"] == receipt["receipt_id"]
    assert call_kwargs["event_type"] == "mcp_v2_action_executed"


# ============================================================================
# TEST CASE 3: Fabricated / Replayed Session or Spent Nonce -> Perimeter Rejection
# ============================================================================

def test_case_3a_fabricated_mcp_session_rejected_at_perimeter_before_cappo():
    """Test Case 3A: Fabricated session -> deterministic rejection prior to calling CAPPO.

    Proves:
    1. Adapter rejects fabricated session immediately at perimeter.
    2. CAPPO pre_execution_assessment is NEVER called.
    3. Consequence executor is NEVER called.
    """
    mock_stack = MagicMock()
    adapter = create_mcp_adapter(stack=mock_stack)
    executor = MockConsequenceExecutor()

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-fabricated-01",
        "method": "tools/call",
        "params": {
            "name": "service.status",
            "arguments": {},
            "_meta": {
                "nonce": "nonce-fresh-1122334455667788",
                "session_id": "sess-forged-999",
            },
        },
    }

    headers = {
        "Mcp-Session-Id": "sess-forged-999",
        "X-Nonce": "nonce-fresh-1122334455667788",
    }

    # Session flagged invalid/fabricated
    session_auth = {
        "valid_session": False,
        "authenticated": False,
        "session_id": "sess-forged-999",
    }

    response = adapter.handle_tool_call(
        raw_request=raw_request,
        headers=headers,
        execution_handler=executor.execute,
        session_auth=session_auth,
    )

    # Invariant: CAPPO decision engine was NEVER invoked
    mock_stack.pre_execution_assessment.assert_not_called()

    # Invariant: Consequence did not execute
    assert executor.call_count == 0

    # Verify perimeter error response
    assert response["error"]["code"] == -32001
    assert "rejected at perimeter" in response["error"]["message"].lower() or "invalid" in response["error"]["message"].lower()


def test_case_3b_replayed_nonce_rejected_at_perimeter_before_cappo():
    """Test Case 3B: Replayed nonce -> deterministic rejection prior to calling CAPPO.

    Proves:
    1. First request with fresh nonce passes perimeter.
    2. Second request with identical nonce is intercepted by atomic anti-replay guard.
    3. CAPPO decision engine is NOT called on the replayed request.
    4. Consequence executor call count does NOT increment on the replayed request.
    """
    mock_stack = MagicMock()
    mock_stack.pre_execution_assessment.return_value = {
        "allow": True,
        "evidence_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "safety": {"recommended_action": "log"},
        "intelligence": {"risk_score": 0.1},
        "governance": {"policy_allows": True},
    }

    adapter = create_mcp_adapter(stack=mock_stack)
    executor = MockConsequenceExecutor()

    shared_nonce = "nonce-replayed-fixed-443322110099"

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-replay-original",
        "method": "tools/call",
        "params": {
            "name": "service.restart",
            "arguments": {"service": "mesh"},
            "_meta": {
                "nonce": shared_nonce,
                "session_id": "sess-valid-777",
            },
        },
    }

    headers = {
        "Mcp-Session-Id": "sess-valid-777",
        "X-Nonce": shared_nonce,
    }

    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "session_id": "sess-valid-777",
    }

    # First attempt: succeeds past perimeter
    resp1 = adapter.handle_tool_call(
        raw_request=raw_request,
        headers=headers,
        execution_handler=executor.execute,
        session_auth=session_auth,
    )
    assert resp1.get("result", {}).get("status") == "success"
    assert executor.call_count == 1
    assert mock_stack.pre_execution_assessment.call_count == 1

    # Second attempt with identical nonce (Replay Attack)
    raw_request_replay = {
        "jsonrpc": "2.0",
        "id": "req-replay-attack",
        "method": "tools/call",
        "params": {
            "name": "service.restart",
            "arguments": {"service": "mesh"},
            "_meta": {
                "nonce": shared_nonce,
                "session_id": "sess-valid-777",
            },
        },
    }

    resp2 = adapter.handle_tool_call(
        raw_request=raw_request_replay,
        headers=headers,
        execution_handler=executor.execute,
        session_auth=session_auth,
    )

    # Invariant: Consequence call count did NOT increase
    assert executor.call_count == 1

    # Invariant: CAPPO was NOT invoked for the replayed request
    assert mock_stack.pre_execution_assessment.call_count == 1

    # Verify structured replay rejection
    assert resp2["error"]["code"] == -32003
    assert resp2["error"]["data"]["error_type"] == "LAW0_REPLAY_DETECTED"
    assert "already been spent" in resp2["error"]["message"].lower() or "reuse" in resp2["error"]["message"].lower()


# ============================================================================
# 8-WAY INTERSECTION INVARIANT TESTS
# ============================================================================

@pytest.mark.parametrize("failing_dimension", [
    "identity",
    "connection",
    "capability",
    "operation",
    "execution_identity",
    "policy",
    "lifecycle",
    "cappo_authorization",
])
def test_8way_intersection_empty_if_any_single_component_fails(failing_dimension: str):
    """Prove that authority = identity ∩ connection ∩ capability ∩ operation
    ∩ execution identity ∩ policy ∩ lifecycle ∩ current CAPPO authorization.

    If ANY of the 8 is invalid/false, effective authority is False, and execution is denied.
    """
    adapter = create_mcp_adapter()
    executor = MockConsequenceExecutor()

    # Baseline valid contexts
    conn_ctx = ConnectionContext(
        connection_id="conn-100",
        actor_id="agent-100",
        intent="test",
        idempotency_key="idem-100",
        status="active",
    )

    cap_ctx = CapabilityContext(
        tenant_id="tenant-100",
        actor_id="agent-100",
        scopes=["service.read"],
        capability_id="service.read",
        action="service.read",
        payload={"arg1": "val1"},
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )

    cappo_assessment = {
        "allow": True,
        "evidence_hash": "abc123hash",
        "governance": {"policy_allows": True},
        "safety": {"recommended_action": "log"},
    }

    session_auth: Dict[str, Any] = {
        "valid_session": True,
        "authenticated": True,
        "status": "active",
    }

    # Inject specific failure according to parameter
    if failing_dimension == "identity":
        session_auth["authenticated"] = False
    elif failing_dimension == "connection":
        conn_ctx.status = "revoked"
    elif failing_dimension == "capability":
        cap_ctx.scopes = ["unrelated.scope"]
    elif failing_dimension == "operation":
        cap_ctx.payload = {f"k_{i}": f"v_{i}" for i in range(100)}  # exceeds MAX_ARGUMENT_KEYS (64)
    elif failing_dimension == "execution_identity":
        cap_ctx.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    elif failing_dimension == "policy":
        cappo_assessment["governance"]["policy_allows"] = False
    elif failing_dimension == "lifecycle":
        session_auth["status"] = "suspended"
    elif failing_dimension == "cappo_authorization":
        cappo_assessment["allow"] = False

    # Evaluate 8-way intersection
    result = adapter.evaluate_8way_intersection(
        connection_ctx=conn_ctx,
        capability_ctx=cap_ctx,
        cappo_assessment=cappo_assessment,
        session_auth=session_auth,
    )

    assert result.effective_authority is False
    assert len(result.failure_reasons) > 0

    # Ensure dispatch raises GovernanceDeniedError and halts consequence
    with pytest.raises(GovernanceDeniedError):
        adapter.dispatch_execution(
            connection_ctx=conn_ctx,
            capability_ctx=cap_ctx,
            execution_handler=executor.execute,
            assessment=cappo_assessment,
            session_auth=session_auth,
        )

    assert executor.call_count == 0


# ============================================================================
# PAYLOAD BOUNDARY & PERIMETER TESTS
# ============================================================================

def test_payload_size_exceeding_64kib_rejected_at_normalization():
    """Verify that arguments payload exceeding 64 KiB is rejected immediately."""
    adapter = create_mcp_adapter()

    oversized_arguments = {
        "large_blob": "X" * (70 * 1024),  # 70 KiB
    }

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-oversized",
        "method": "tools/call",
        "params": {
            "name": "service.upload",
            "arguments": oversized_arguments,
            "_meta": {
                "nonce": "nonce-oversized-1122334455",
            },
        },
    }

    headers = {"X-Nonce": "nonce-oversized-1122334455"}

    response = adapter.handle_tool_call(
        raw_request=raw_request,
        headers=headers,
    )

    assert response["error"]["code"] == -32602
    assert "exceeds maximum allowed size" in response["error"]["message"]


def test_short_nonce_rejected_at_perimeter():
    """Verify that nonces shorter than 16 characters are rejected at perimeter."""
    adapter = create_mcp_adapter()

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-short-nonce",
        "method": "tools/call",
        "params": {
            "name": "service.status",
            "arguments": {},
            "_meta": {
                "nonce": "short",  # < 16 chars
            },
        },
    }

    response = adapter.handle_tool_call(
        raw_request=raw_request,
        headers={"X-Nonce": "short"},
    )

    assert response["error"]["code"] == -32003
    assert response["error"]["data"]["error_type"] == "LAW0_NONCE_INVALID"


# ============================================================================
# CONCURRENT NONCE REPLAY ATTACK MITIGATION
# ============================================================================

def test_concurrent_nonce_replay_attack_race_condition():
    """Verify thread-safety of atomic single-use nonce burn under concurrent race condition.

    10 threads concurrently submit requests with the SAME nonce.
    Exactly 1 thread must succeed past perimeter; 9 must be rejected with LAW0_REPLAY_DETECTED.
    """
    adapter = create_mcp_adapter()
    shared_nonce = "nonce-concurrent-race-11223344556677"

    results: list[dict[str, Any]] = []

    def attempt_request(thread_idx: int) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": f"req-race-{thread_idx}",
            "method": "tools/call",
            "params": {
                "name": "service.ping",
                "arguments": {},
                "_meta": {
                    "nonce": shared_nonce,
                    "session_id": "sess-race",
                },
            },
        }
        headers = {
            "Mcp-Session-Id": "sess-race",
            "X-Nonce": shared_nonce,
        }
        session_auth = {
            "valid_session": True,
            "authenticated": True,
            "session_id": "sess-race",
        }
        return adapter.handle_tool_call(
            raw_request=req,
            headers=headers,
            session_auth=session_auth,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(attempt_request, i) for i in range(10)]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    success_count = sum(1 for r in results if r.get("result", {}).get("status") == "success")
    replay_count = sum(
        1 for r in results
        if r.get("error", {}).get("data", {}).get("error_type") == "LAW0_REPLAY_DETECTED"
    )

    assert success_count == 1, f"Expected exactly 1 success, got {success_count}"
    assert replay_count == 9, f"Expected exactly 9 replay rejections, got {replay_count}"


# ============================================================================
# SEMANTIC TRANSLATION OF MCPAPI V2 MODELS
# ============================================================================

def test_semantic_normalization_populates_mcpapi_v2_models():
    """Verify that normalize_request produces valid ConnectionContext, CapabilityContext,
    VeklomAgent, and AuthorityBundle models from mcpapi_v2.
    """
    adapter = create_mcp_adapter()

    raw_request = {
        "jsonrpc": "2.0",
        "id": "req-norm-test",
        "method": "tools/call",
        "params": {
            "name": "cloud.container.deploy",
            "arguments": {"image": "nginx:alpine", "port": 80},
            "_meta": {
                "connection_id": "conn-custom-987",
                "session_id": "sess-custom-654",
                "idempotency_key": "idem-custom-321",
                "intent": "Deploy web proxy container",
                "tenant_id": "tenant-enterprise-1",
                "agent_id": "agent-devops-01",
                "risk_tier": "high",
                "scopes": ["cloud.container.deploy", "cloud.container.stop"],
            },
        },
    }

    conn_ctx, cap_ctx, agent, bundle = adapter.normalize_request(raw_request=raw_request)

    # ConnectionContext check
    assert isinstance(conn_ctx, ConnectionContext)
    assert conn_ctx.connection_id == "conn-custom-987"
    assert conn_ctx.actor_id == "agent-devops-01"
    assert conn_ctx.transport == "mcp"
    assert conn_ctx.protocol_version == "2.0"
    assert conn_ctx.intent == "Deploy web proxy container"
    assert conn_ctx.idempotency_key == "idem-custom-321"

    # CapabilityContext check
    assert isinstance(cap_ctx, CapabilityContext)
    assert cap_ctx.tenant_id == "tenant-enterprise-1"
    assert cap_ctx.actor_id == "agent-devops-01"
    assert cap_ctx.scopes == ["cloud.container.deploy", "cloud.container.stop"]
    assert cap_ctx.risk_tier == "high"
    assert cap_ctx.capability_id == "cloud.container.deploy"
    assert cap_ctx.payload == {"image": "nginx:alpine", "port": 80}

    # VeklomAgent model check
    assert isinstance(agent, VeklomAgent)
    assert agent.agent_id == "agent-devops-01"
    assert agent.owner_id == "tenant-enterprise-1"

    # AuthorityBundle model check
    assert isinstance(bundle, AuthorityBundle)
    assert bundle.agent_id == "agent-devops-01"
    assert "cloud.container.deploy" in bundle.mcp_tools
    assert isinstance(bundle.permissions, AuthorityPermissions)
    assert bundle.permissions.can_execute is True
