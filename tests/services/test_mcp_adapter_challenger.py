"""Independent Empirical Challenger Test Suite for Cappy MCP v2 Semantic Adapter.
Author: challenger_track_b_1
Role: critic, specialist (Veklom Doctrine)

Executes empirical attacks on Track B:
1. Consequence bypass attempts on DENY and string/type truthiness anomalies.
2. High-concurrency multi-threaded replay attacks against validate_perimeter (50+ threads).
3. Payload and nonce boundary fuzzing (exact 64 KiB boundary, 65 keys, non-string types, malformed RPC).
4. 8-way intersection dimension invalidation (exhaustive single and compound invalidations).
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.governance import Policy, PolicyRule
from cappo_backend.services.mcp_adapter import (
    CapabilityContext,
    ConnectionContext,
    GovernanceDeniedError,
    InvalidSessionError,
    MCPv2Adapter,
    PayloadBoundExceededError,
    PerimeterDefenseError,
    ReplayAttackError,
    create_mcp_adapter,
)


class ChallengerSpyExecutor:
    """Spy executor tracking invocations, arguments, and preventing silent execution."""
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
            "call_index": self.call_count,
            "connection_id": connection_ctx.connection_id,
            "capability_id": capability_ctx.capability_id,
            "payload": capability_ctx.payload,
            "kwargs": kwargs,
        }
        self.invocations.append(record)
        return {"status": "executed", "call_count": self.call_count}


# ============================================================================
# CATEGORY 1: CONSEQUENCE BYPASS ATTACKS ON DENY
# ============================================================================

def test_challenger_consequence_bypass_on_explicit_deny():
    """Attack 1.1: Verify that when CAPPO pre-execution assessment returns allow=False,
    NO consequence executes and NO action receipt is emitted under any circumstances.
    """
    adapter = create_mcp_adapter()
    spy = ChallengerSpyExecutor()

    deny_policy = Policy(
        policy_id="policy-hard-deny",
        rules=[PolicyRule(effect="deny", description="Hard block on capability")],
    )

    req = {
        "jsonrpc": "2.0",
        "id": "challenger-bypass-01",
        "method": "tools/call",
        "params": {
            "name": "system.reboot",
            "arguments": {"force": True},
            "_meta": {
                "nonce": "nonce-probe1-11223344556677",
                "session_id": "sess-probe1-valid",
                "agent_id": "agent-probe1",
            },
        },
    }
    headers = {
        "Mcp-Session-Id": "sess-probe1-valid",
        "X-Nonce": "nonce-probe1-11223344556677",
        "X-Agent-ID": "agent-probe1",
    }
    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "session_id": "sess-probe1-valid",
        "status": "active",
    }

    resp = adapter.handle_tool_call(
        raw_request=req,
        headers=headers,
        execution_handler=spy.execute,
        session_auth=session_auth,
        system_policy=deny_policy,
    )

    # Invariant: ZERO consequence invocations
    assert spy.call_count == 0
    assert len(spy.invocations) == 0

    # Invariant: Structured error returned, not result
    assert "result" not in resp
    assert "error" in resp
    assert resp["error"]["code"] == -32003
    assert resp["error"]["message"] == "CAPPO_GOVERNANCE_DENIED"

    # Invariant: No receipt emitted
    data = resp["error"]["data"]
    assert "receipt" not in data
    assert data["intersection"]["effective_authority"] is False
    assert data["intersection"]["cappo_authorization"] is False


def test_challenger_consequence_bypass_truthiness_probe():
    """Attack 1.2: Probe how evaluate_8way_intersection handles non-boolean truthiness
    in cappo_assessment['allow'].
    """
    adapter = create_mcp_adapter()

    conn_ctx = ConnectionContext(
        connection_id="conn-truthiness",
        actor_id="agent-truthiness",
        intent="probe",
        idempotency_key="idem-truthiness",
        status="active",
    )
    cap_ctx = CapabilityContext(
        tenant_id="tenant-probe",
        actor_id="agent-truthiness",
        scopes=["exec"],
        capability_id="exec",
        action="exec",
        payload={},
    )

    # 1. Strict boolean False: must fail
    res_bool_false = adapter.evaluate_8way_intersection(
        connection_ctx=conn_ctx,
        capability_ctx=cap_ctx,
        cappo_assessment={"allow": False},
    )
    assert res_bool_false.effective_authority is False
    assert res_bool_false.cappo_authorized is False


# ============================================================================
# EMPIRICAL VULNERABILITY REPRODUCTIONS (FINDINGS REQUIRING REMEDIATION)
# ============================================================================

def test_vulnerability_1_consequence_bypass_on_string_deny():
    """VULNERABILITY REPRODUCTION 1:
    When CAPPO assessment emits allow='deny' or allow='False' (e.g. from JSON serialization
    or external policy engine), bool('deny') evaluates to True in Python.
    This bypasses CAPPO DENY and executes the consequence!
    """
    adapter = create_mcp_adapter()
    spy = ChallengerSpyExecutor()

    conn_ctx = ConnectionContext(
        connection_id="conn-vuln-1",
        actor_id="agent-vuln-1",
        intent="consequence_probe",
        idempotency_key="idem-vuln-1",
        status="active",
    )
    cap_ctx = CapabilityContext(
        tenant_id="tenant-vuln-1",
        actor_id="agent-vuln-1",
        scopes=["service.exec"],
        capability_id="service.exec",
        action="service.exec",
        payload={"danger": True},
    )

    # Upstream CAPPO returns decision 'deny' as a string in allow field
    assessment = {
        "allow": "deny",  # Should mean DENY!
        "governance": {"policy_allows": True},
    }

    # Under strict consequence dominance, this MUST raise GovernanceDeniedError
    # and spy.call_count MUST remain 0.
    # IN CURRENT CODE: effective_authority is True and spy.execute runs!
    with pytest.raises(GovernanceDeniedError):
        adapter.dispatch_execution(
            connection_ctx=conn_ctx,
            capability_ctx=cap_ctx,
            execution_handler=spy.execute,
            assessment=assessment,
        )

    assert spy.call_count == 0, f"Consequence executed {spy.call_count} times despite DENY!"


def test_vulnerability_2_unauthenticated_request_bypasses_when_session_auth_none():
    """VULNERABILITY REPRODUCTION 2:
    When session_auth is None (the default), perimeter verification skips session checks,
    identity_valid defaults to True, and lifecycle_valid defaults to True.
    An unauthenticated caller with no session credentials can execute arbitrary consequences!
    """
    adapter = create_mcp_adapter()
    spy = ChallengerSpyExecutor()

    req = {
        "jsonrpc": "2.0",
        "id": "unauth-probe",
        "method": "tools/call",
        "params": {
            "name": "system.exec",
            "arguments": {"cmd": "whoami"},
            "_meta": {
                "nonce": "nonce-unauth-1122334455667788",
                "agent_id": "unauthenticated-attacker",
            },
        },
    }
    headers = {
        "X-Nonce": "nonce-unauth-1122334455667788",
        # Notice: No session ID, no bearer token
    }

    # Under zero-trust doctrine, unauthenticated requests with no session_auth MUST be rejected
    resp = adapter.handle_tool_call(
        raw_request=req,
        headers=headers,
        execution_handler=spy.execute,
        session_auth=None,  # Missing authenticated session context
    )

    # IN CURRENT CODE: resp['result']['status'] is 'success' and spy.call_count is 1!
    assert "error" in resp, "Unauthenticated request was allowed to execute!"
    assert spy.call_count == 0, "Consequence executed without session authentication!"


def test_vulnerability_3_non_dict_raw_request_crashes_with_attribute_error():
    """VULNERABILITY REPRODUCTION 3:
    When raw_request is not a dictionary (e.g. None or JSON-RPC batch list),
    validate_perimeter calls raw_request.get('params') which raises AttributeError.
    Because AttributeError is not caught by except PerimeterDefenseError,
    it crashes handle_tool_call instead of returning a JSON-RPC error.
    """
    adapter = create_mcp_adapter()

    # handle_tool_call should return a JSON-RPC error response, NOT raise unhandled AttributeError
    resp = adapter.handle_tool_call(
        raw_request=None,
        headers={"X-Nonce": "nonce-test-1122334455667788"},
    )
    assert resp["error"]["code"] in (-32600, -32700, -32602)



def test_challenger_dispatch_execution_direct_rejection():
    """Attack 1.3: Direct invocation of dispatch_execution with failing intersection.
    Verify GovernanceDeniedError is raised and execution_handler is NEVER called.
    """
    adapter = create_mcp_adapter()
    spy = ChallengerSpyExecutor()

    conn_ctx = ConnectionContext(
        connection_id="conn-direct",
        actor_id="agent-direct",
        intent="direct_probe",
        idempotency_key="idem-direct",
        status="active",
    )
    cap_ctx = CapabilityContext(
        tenant_id="tenant-direct",
        actor_id="agent-direct",
        scopes=["other_tool"],  # Mismatched scope
        capability_id="target_tool",
        action="target_tool",
        payload={},
    )

    with pytest.raises(GovernanceDeniedError) as exc_info:
        adapter.dispatch_execution(
            connection_ctx=conn_ctx,
            capability_ctx=cap_ctx,
            execution_handler=spy.execute,
            assessment={"allow": True, "governance": {"policy_allows": True}},
        )

    assert spy.call_count == 0
    assert "capability" in exc_info.value.reason


# ============================================================================
# CATEGORY 2: MULTI-THREADED CONCURRENT REPLAY ATTACKS
# ============================================================================

def test_challenger_high_concurrency_50_threads_replay_race():
    """Attack 2.1: 50 concurrent threads race the EXACT same nonce simultaneously.
    Proves that exactly 1 request penetrates the perimeter and 49 are intercepted
    with LAW0_REPLAY_DETECTED under heavy concurrency.
    """
    adapter = create_mcp_adapter()
    shared_nonce = "nonce-stress-race-50-threads-9988776655"
    results: list[dict[str, Any]] = []

    def attack_thread(idx: int) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": f"race-req-{idx}",
            "method": "tools/call",
            "params": {
                "name": "system.ping",
                "arguments": {"thread_id": idx},
                "_meta": {
                    "nonce": shared_nonce,
                    "session_id": "sess-stress",
                },
            },
        }
        headers = {
            "Mcp-Session-Id": "sess-stress",
            "X-Nonce": shared_nonce,
        }
        session_auth = {
            "valid_session": True,
            "authenticated": True,
            "session_id": "sess-stress",
            "status": "active",
        }
        return adapter.handle_tool_call(
            raw_request=req,
            headers=headers,
            session_auth=session_auth,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(attack_thread, i) for i in range(50)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_responses = [r for r in results if r.get("result", {}).get("status") == "success"]
    replay_responses = [
        r for r in results
        if r.get("error", {}).get("data", {}).get("error_type") == "LAW0_REPLAY_DETECTED"
    ]

    assert len(success_responses) == 1, f"Expected exactly 1 success, got {len(success_responses)}"
    assert len(replay_responses) == 49, f"Expected exactly 49 replays, got {len(replay_responses)}"


def test_challenger_whitespace_normalized_replay_race():
    """Attack 2.2: Concurrent threads submit nonces that are identical after strip()
    (e.g., leading/trailing tabs, spaces, newlines).
    Verify that whitespace variations cannot bypass the replay detector.
    """
    adapter = create_mcp_adapter()
    raw_base = "nonce-whitespace-variant-11223344"
    variants = [
        f"  {raw_base}  ",
        f"\t{raw_base}\t",
        f"\n{raw_base}\n",
        f" \t {raw_base} \t ",
        raw_base,
        f"{raw_base} ",
        f" {raw_base}",
        f"\r\n{raw_base}\r\n",
        f"  {raw_base}",
        f"{raw_base}  ",
    ]
    results: list[dict[str, Any]] = []

    def attack_variant(variant_nonce: str, idx: int) -> dict[str, Any]:
        req = {
            "jsonrpc": "2.0",
            "id": f"ws-req-{idx}",
            "method": "tools/call",
            "params": {
                "name": "system.ping",
                "arguments": {},
                "_meta": {"nonce": variant_nonce, "session_id": "sess-ws"},
            },
        }
        headers = {
            "Mcp-Session-Id": "sess-ws",
            "X-Nonce": variant_nonce,
        }
        session_auth = {
            "valid_session": True,
            "authenticated": True,
            "session_id": "sess-ws",
            "status": "active",
        }
        return adapter.handle_tool_call(
            raw_request=req,
            headers=headers,
            session_auth=session_auth,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(attack_variant, variants[i], i) for i in range(len(variants))]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_responses = [r for r in results if r.get("result", {}).get("status") == "success"]
    replay_responses = [
        r for r in results
        if r.get("error", {}).get("data", {}).get("error_type") == "LAW0_REPLAY_DETECTED"
    ]

    assert len(success_responses) == 1, f"Expected 1 success, got {len(success_responses)}"
    assert len(replay_responses) == 9, f"Expected 9 replays, got {len(replay_responses)}"


def test_challenger_multi_nonce_parallel_blast():
    """Attack 2.3: 10 different nonces attacked by 5 threads each (50 threads total).
    Verify exactly 10 successes and 40 replay rejections.
    """
    adapter = create_mcp_adapter()
    results: list[dict[str, Any]] = []

    def attack_cell(nonce_id: int, thread_id: int) -> dict[str, Any]:
        nonce_val = f"nonce-cell-{nonce_id:04d}-1122334455667788"
        req = {
            "jsonrpc": "2.0",
            "id": f"blast-{nonce_id}-{thread_id}",
            "method": "tools/call",
            "params": {
                "name": "service.status",
                "arguments": {},
                "_meta": {"nonce": nonce_val, "session_id": f"sess-{nonce_id}"},
            },
        }
        headers = {"Mcp-Session-Id": f"sess-{nonce_id}", "X-Nonce": nonce_val}
        session_auth = {
            "valid_session": True,
            "authenticated": True,
            "session_id": f"sess-{nonce_id}",
            "status": "active",
        }
        return adapter.handle_tool_call(
            raw_request=req,
            headers=headers,
            session_auth=session_auth,
        )

    tasks = []
    for n in range(10):
        for t in range(5):
            tasks.append((n, t))

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as pool:
        futures = [pool.submit(attack_cell, n, t) for n, t in tasks]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    success_responses = [r for r in results if r.get("result", {}).get("status") == "success"]
    replay_responses = [
        r for r in results
        if r.get("error", {}).get("data", {}).get("error_type") == "LAW0_REPLAY_DETECTED"
    ]

    assert len(success_responses) == 10
    assert len(replay_responses) == 40


# ============================================================================
# CATEGORY 3: MALFORMED / BOUNDARY FUZZING
# ============================================================================

@pytest.mark.parametrize("invalid_nonce", [
    "",
    "   ",
    "short_nonce",
    "123456789012345",  # 15 chars
    "       12345       ",  # stripped < 16 chars
    None,
    1234567890123456,   # integer type
    ["nonce_in_list_12345678"],
    {"nonce": "nonce_in_dict_12345"},
    True,
])
def test_challenger_fuzz_nonce_bounds_and_types(invalid_nonce: Any):
    """Attack 3.1: Fuzz nonce field with boundary lengths and non-string types.
    Verify perimeter cleanly rejects with LAW0_NONCE_INVALID (-32003).
    """
    adapter = create_mcp_adapter()

    raw_request = {
        "jsonrpc": "2.0",
        "id": "fuzz-nonce",
        "method": "tools/call",
        "params": {
            "name": "service.check",
            "arguments": {},
            "_meta": {"nonce": invalid_nonce},
        },
    }

    # Pass either in headers or meta
    headers = {}
    if isinstance(invalid_nonce, str):
        headers["X-Nonce"] = invalid_nonce

    resp = adapter.handle_tool_call(raw_request=raw_request, headers=headers)

    assert "error" in resp
    assert resp["error"]["code"] == -32003
    assert resp["error"]["data"]["error_type"] == "LAW0_NONCE_INVALID"


def test_challenger_payload_exact_boundary_64kib():
    """Attack 3.2: Exact boundary test on 64 KiB:
    Payload serialized to 64 KiB (65,536 bytes) -> PASSES bounds check.
    Payload serialized to 64 KiB + 1 byte -> REJECTED at normalization.
    """
    adapter = create_mcp_adapter()

    # Determine base overhead of JSON serialization: {"blob": "..."}
    overhead = len(json.dumps({"blob": ""}).encode("utf-8"))
    fill_len_allowed = (64 * 1024) - overhead

    # 1. Exactly 64 KiB
    valid_blob = "A" * fill_len_allowed
    exact_payload = {"blob": valid_blob}
    assert len(json.dumps(exact_payload).encode("utf-8")) == 64 * 1024

    req_exact = {
        "jsonrpc": "2.0",
        "id": "exact-64k",
        "method": "tools/call",
        "params": {
            "name": "data.process",
            "arguments": exact_payload,
            "_meta": {"nonce": "nonce-boundary-exact-64k-12345"},
        },
    }
    headers_exact = {"X-Nonce": "nonce-boundary-exact-64k-12345"}
    session_auth = {"valid_session": True, "authenticated": True, "status": "active"}

    resp_exact = adapter.handle_tool_call(
        raw_request=req_exact,
        headers=headers_exact,
        session_auth=session_auth,
    )
    # Exactly 64 KiB must not trigger PAYLOAD_BOUND_EXCEEDED
    if "error" in resp_exact:
        assert resp_exact["error"].get("data", {}).get("error_type") != "PAYLOAD_BOUND_EXCEEDED"

    # 2. 64 KiB + 1 byte
    overflow_blob = "A" * (fill_len_allowed + 1)
    overflow_payload = {"blob": overflow_blob}
    assert len(json.dumps(overflow_payload).encode("utf-8")) == (64 * 1024) + 1

    req_overflow = {
        "jsonrpc": "2.0",
        "id": "overflow-64k",
        "method": "tools/call",
        "params": {
            "name": "data.process",
            "arguments": overflow_payload,
            "_meta": {"nonce": "nonce-boundary-overflow-64k-123"},
        },
    }
    headers_overflow = {"X-Nonce": "nonce-boundary-overflow-64k-123"}

    resp_overflow = adapter.handle_tool_call(
        raw_request=req_overflow,
        headers=headers_overflow,
        session_auth=session_auth,
    )
    assert resp_overflow["error"]["code"] == -32602
    assert "exceeds maximum allowed size" in resp_overflow["error"]["message"]


def test_challenger_payload_key_count_boundary_64_vs_65():
    """Attack 3.3: Payload arguments key count boundary:
    64 keys -> allowed.
    65 keys -> rejected with PAYLOAD_BOUND_EXCEEDED (-32602).
    """
    adapter = create_mcp_adapter()
    session_auth = {"valid_session": True, "authenticated": True, "status": "active"}

    # 64 keys
    args_64 = {f"k_{i}": i for i in range(64)}
    req_64 = {
        "jsonrpc": "2.0",
        "id": "keys-64",
        "method": "tools/call",
        "params": {
            "name": "service.config",
            "arguments": args_64,
            "_meta": {"nonce": "nonce-boundary-keys-64-11223344"},
        },
    }
    resp_64 = adapter.handle_tool_call(
        raw_request=req_64,
        headers={"X-Nonce": "nonce-boundary-keys-64-11223344"},
        session_auth=session_auth,
    )
    if "error" in resp_64:
        assert resp_64["error"].get("data", {}).get("error_type") != "PAYLOAD_BOUND_EXCEEDED"

    # 65 keys
    args_65 = {f"k_{i}": i for i in range(65)}
    req_65 = {
        "jsonrpc": "2.0",
        "id": "keys-65",
        "method": "tools/call",
        "params": {
            "name": "service.config",
            "arguments": args_65,
            "_meta": {"nonce": "nonce-boundary-keys-65-11223344"},
        },
    }
    resp_65 = adapter.handle_tool_call(
        raw_request=req_65,
        headers={"X-Nonce": "nonce-boundary-keys-65-11223344"},
        session_auth=session_auth,
    )
    assert resp_65["error"]["code"] == -32602
    assert "Arguments key count" in resp_65["error"]["message"]


@pytest.mark.parametrize("session_status", [
    "expired",
    "revoked",
    "suspended",
])
def test_challenger_session_status_lifecycle_rejection(session_status: str):
    """Attack 3.4: Verify perimeter rejection of non-active sessions."""
    adapter = create_mcp_adapter()
    req = {
        "jsonrpc": "2.0",
        "id": f"sess-{session_status}",
        "method": "tools/call",
        "params": {
            "name": "service.read",
            "arguments": {},
            "_meta": {"nonce": f"nonce-sess-{session_status}-12345678", "session_id": "sess-dead"},
        },
    }
    headers = {
        "Mcp-Session-Id": "sess-dead",
        "X-Nonce": f"nonce-sess-{session_status}-12345678",
    }
    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "session_id": "sess-dead",
        "status": session_status,
    }

    resp = adapter.handle_tool_call(raw_request=req, headers=headers, session_auth=session_auth)
    assert resp["error"]["code"] == -32001
    assert "INVALID_MCP_SESSION" in resp["error"]["data"]["error_type"]


# ============================================================================
# CATEGORY 4: 8-WAY INTERSECTION EXHAUSTIVE INVALIDATION
# ============================================================================

@pytest.mark.parametrize("dimension,expected_reason_substring", [
    ("identity_missing_actor", "Actor identity missing"),
    ("identity_anonymous", "Actor identity missing"),
    ("identity_unauthenticated", "Actor identity missing"),
    ("connection_inactive", "is not active"),
    ("connection_empty_id", "is not active"),
    ("capability_out_of_scope", "not covered in scopes"),
    ("operation_excessive_keys", "Arguments exceed parameter bounds"),
    ("execution_identity_expired", "Context deadline has expired"),
    ("policy_denied", "policy denies action"),
    ("lifecycle_suspended", "lifecycle is terminated/suspended"),
    ("lifecycle_revoked", "lifecycle is terminated/suspended"),
    ("cappo_denied", "CAPPO pre-execution assessment returned allow=False"),
])
def test_challenger_8way_intersection_exhaustive_invalidation(
    dimension: str,
    expected_reason_substring: str,
):
    """Attack 4.1: Exhaustively invalidate each single dimension and verify that
    effective_authority is strictly False and the failure reasons accurately identify the cause.
    """
    adapter = create_mcp_adapter()

    conn_ctx = ConnectionContext(
        connection_id="conn-baseline",
        actor_id="agent-baseline",
        intent="baseline",
        idempotency_key="idem-baseline",
        status="active",
    )
    cap_ctx = CapabilityContext(
        tenant_id="tenant-baseline",
        actor_id="agent-baseline",
        scopes=["exec.read"],
        capability_id="exec.read",
        action="exec.read",
        payload={"query": "test"},
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    cappo_assessment = {
        "allow": True,
        "evidence_hash": "hash-baseline",
        "governance": {"policy_allows": True},
    }
    session_auth = {
        "valid_session": True,
        "authenticated": True,
        "status": "active",
    }

    # Invalidate dimension
    if dimension == "identity_missing_actor":
        conn_ctx.actor_id = ""
    elif dimension == "identity_anonymous":
        conn_ctx.actor_id = "anonymous"
    elif dimension == "identity_unauthenticated":
        session_auth["authenticated"] = False
    elif dimension == "connection_inactive":
        conn_ctx.status = "closed"
    elif dimension == "connection_empty_id":
        conn_ctx.connection_id = ""
    elif dimension == "capability_out_of_scope":
        cap_ctx.scopes = ["exec.write"]
    elif dimension == "operation_excessive_keys":
        cap_ctx.payload = {f"k_{i}": i for i in range(70)}
    elif dimension == "execution_identity_expired":
        cap_ctx.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    elif dimension == "policy_denied":
        cappo_assessment["governance"]["policy_allows"] = False
    elif dimension == "lifecycle_suspended":
        session_auth["status"] = "suspended"
    elif dimension == "lifecycle_revoked":
        session_auth["status"] = "revoked"
    elif dimension == "cappo_denied":
        cappo_assessment["allow"] = False

    result = adapter.evaluate_8way_intersection(
        connection_ctx=conn_ctx,
        capability_ctx=cap_ctx,
        cappo_assessment=cappo_assessment,
        session_auth=session_auth,
    )

    assert result.effective_authority is False
    assert any(expected_reason_substring in r for r in result.failure_reasons), (
        f"Expected '{expected_reason_substring}' in failure reasons: {result.failure_reasons}"
    )


def test_challenger_compound_multi_dimension_invalidation():
    """Attack 4.2: Invalidate 3 dimensions simultaneously (identity, policy, cappo_authorization).
    Verify all 3 failures are recorded and effective_authority is False.
    """
    adapter = create_mcp_adapter()
    conn_ctx = ConnectionContext(
        connection_id="conn-compound",
        actor_id="anonymous",  # Invalid identity
        intent="compound",
        idempotency_key="idem-compound",
        status="active",
    )
    cap_ctx = CapabilityContext(
        tenant_id="tenant-compound",
        actor_id="anonymous",
        scopes=["*"],
        capability_id="exec",
        action="exec",
        payload={},
    )
    cappo_assessment = {
        "allow": False,  # Invalid CAPPO
        "governance": {"policy_allows": False},  # Invalid policy
    }
    session_auth = {"authenticated": False}

    result = adapter.evaluate_8way_intersection(
        connection_ctx=conn_ctx,
        capability_ctx=cap_ctx,
        cappo_assessment=cappo_assessment,
        session_auth=session_auth,
    )

    assert result.effective_authority is False
    assert len(result.failure_reasons) >= 3
    assert any("identity" in r for r in result.failure_reasons)
    assert any("policy" in r for r in result.failure_reasons)
    assert any("cappo_authorization" in r for r in result.failure_reasons)
