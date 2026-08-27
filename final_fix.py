import os

g1_2_code = '''
\"\"\"
G1.2 - WAN OFF: Expired capability fails locally
\"\"\"
import contextlib
import socket
import uuid
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session
from sqlalchemy import text

from cappo_backend.capability_mount.models import Decision, MountPolicy, MountScope, CapabilityPackage
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models import CapabilityActionReceipt

_LOOPBACK = ("127.", "::1", "localhost")

def _is_loopback(host: str) -> bool:
    return any(str(host).startswith(p) for p in _LOOPBACK)

@contextlib.contextmanager
def no_wan():
    blocked_attempts: list[tuple] = []
    original_connect = socket.socket.connect

    def mock_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback(host):
            blocked_attempts.append(address)
            raise OSError(f"WAN blocked by G1 test: {host}")
        return original_connect(self, address)

    socket.socket.connect = mock_connect
    try:
        yield blocked_attempts
    finally:
        socket.socket.connect = original_connect

CALLER_SPIFFE = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/my-agent"
CAPABILITY_ID = "echo@v1"
ACTION = "echo"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.services.mount_pgl import AuditPGLAnchor
    from cappo_backend.config import Settings
    
    settings = Settings(
        pgl_ledger_url="http://1.1.1.1:80", 
        pgl_ledger_timeout_ms=100
    )
    reg = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="G1.X test capability",
            reads=[ACTION],
            writes=[]
        )
    )
    return reg

def test_g1_2_wan_off_expired_authority_fails(db: Session):
    exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    
    reg = _build_registry(db)
    
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=600, 
        owner_principal="auth-disabled",
        execution_id=exec_id,
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce

    # We must expire it in the DB directly, since the pydantic model is frozen
    db.execute(text(f"UPDATE capability_mounts SET expires_at = '2000-01-01 00:00:00' WHERE mount_id = '{mount_id}'"))
    db.commit()

    with no_wan() as blocked:
        decision, dec_reason, _anchor, binding = reg.evaluate(
            mount_id=mount_id,
            action=ACTION,
            token_id=token_id,
            nonce=nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd"*16,
                "trust_domain": "example.org",
            },
        )
        assert decision == Decision.DENY

'''

g1_3_code = '''
\"\"\"
G1.3 - WAN OFF: Wrong scope / Replay fails locally
\"\"\"
import contextlib
import socket
import uuid

import pytest
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import Decision, MountPolicy, MountScope, CapabilityPackage
from cappo_backend.capability_mount.service import MountRegistry

_LOOPBACK = ("127.", "::1", "localhost")

def _is_loopback(host: str) -> bool:
    return any(str(host).startswith(p) for p in _LOOPBACK)

@contextlib.contextmanager
def no_wan():
    blocked_attempts: list[tuple] = []
    original_connect = socket.socket.connect

    def mock_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback(host):
            blocked_attempts.append(address)
            raise OSError(f"WAN blocked by G1 test: {host}")
        return original_connect(self, address)

    socket.socket.connect = mock_connect
    try:
        yield blocked_attempts
    finally:
        socket.socket.connect = original_connect

CALLER_SPIFFE = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/my-agent"
CAPABILITY_ID = "echo@v1"
ACTION = "echo"
WRONG_ACTION = "write"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.services.mount_pgl import AuditPGLAnchor
    from cappo_backend.config import Settings
    
    settings = Settings(
        pgl_ledger_url="http://1.1.1.1:80", 
        pgl_ledger_timeout_ms=100
    )
    reg = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="G1.X test capability",
            reads=[ACTION],
            writes=[]
        )
    )
    return reg

def test_g1_3_wan_off_wrong_scope_fails(db: Session):
    exec_id = f"exec_{uuid.uuid4().hex[:8]}"

    reg = _build_registry(db)
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent", policy=MountPolicy(), ttl_seconds=600,
        owner_principal="auth-disabled", execution_id=exec_id,
        caller_spiffe_id=CALLER_SPIFFE, executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    
    with no_wan() as blocked:
        # Try wrong action offline
        decision, dec_reason, _anchor, binding = reg.evaluate(
            mount_id=mount_record.mount.id, action=WRONG_ACTION, token_id=mount_record.token.token_id, nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE, "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd", "trust_domain": "example.org",
            },
        )
        assert decision == Decision.DENY

def test_g1_3_wan_off_replay_fails(db: Session):
    exec_id = f"exec_{uuid.uuid4().hex[:8]}"

    reg = _build_registry(db)
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent", policy=MountPolicy(), ttl_seconds=600,
        owner_principal="auth-disabled", execution_id=exec_id,
        caller_spiffe_id=CALLER_SPIFFE, executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    
    with no_wan() as blocked:
        # 1st execute (success)
        d1, _, _, _ = reg.evaluate(
            mount_id=mount_record.mount.id, action=ACTION,
            token_id=mount_record.token.token_id, nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE, "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd", "trust_domain": "example.org",
            },
        )
        assert d1 == Decision.ALLOW

        # Replay (fail)
        d2, _, _, _ = reg.evaluate(
            mount_id=mount_record.mount.id, action=ACTION,
            token_id=mount_record.token.token_id, nonce=mount_record.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE, "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd", "trust_domain": "example.org",
            },
        )
        assert d2 == Decision.DENY

'''

with open('tests/test_g1_2_wan_off_expired.py', 'w', encoding='utf-8') as f:
    f.write(g1_2_code.strip() + '\n')
    
with open('tests/test_g1_3_wan_off_scope_replay.py', 'w', encoding='utf-8') as f:
    f.write(g1_3_code.strip() + '\n')
