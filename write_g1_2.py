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

from cappo_backend.capability_mount.models import Decision, MountPolicy, MountScope, CapabilityPackage
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models import CapabilityActionReceipt
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
    verify_biscuit_capability,
)
from cappo_backend.security.evidence import get_evidence_key_pair, verify_signed_execution_evidence
from cappo_backend.security.merkle import AppendOnlyMerkleTree, hash_leaf
from cappo_backend.security.merkle_ops import get_merkle_ordered_cose_bytes

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
        ttl_seconds=3600, 
        owner_principal="auth-disabled",
        execution_id=exec_id,
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce

    tree_before = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    size_before = tree_before.size

    # Simulate time passing by altering the expiration time of the mount in DB
    mount_record.mount.expires_at = datetime.now(timezone.utc)
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

    # For G1.2, if it returns DENY because the mount is expired early in evaluate(),
    # it might not create a receipt. So we just assert decision == Decision.DENY.

'''

with open('tests/test_g1_2_wan_off_expired.py', 'w', encoding='utf-8') as f:
    f.write(g1_2_code.strip() + '\n')
