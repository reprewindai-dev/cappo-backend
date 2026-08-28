import contextlib
import socket

from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import (
    Decision,
    MountPolicy,
    MountScope,
)
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.models import CapabilityActionReceipt
from cappo_backend.security.biscuit import (
    attenuate_biscuit_capability,
    mint_biscuit_capability,
)
from cappo_backend.security.evidence import (
    get_evidence_key_pair,
    verify_signed_execution_evidence,
)
from cappo_backend.security.merkle import AppendOnlyMerkleTree, verify_consistency_proof
from cappo_backend.security.merkle_ops import get_merkle_ordered_cose_bytes


@contextlib.contextmanager
def no_wan():
    original_connect = socket.socket.connect
    blocked_attempts = []

    def mock_connect(self, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', '::1', 'localhost'):
            blocked_attempts.append(address)
            raise OSError(f"WAN blocked by G1 test: {address}")
        return original_connect(self, address)

    socket.socket.connect = mock_connect
    try:
        yield blocked_attempts
    finally:
        socket.socket.connect = original_connect


CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/g1-agent"

CAPABILITY_ID  = "echo@v1"
EXEC_ID        = "g1-exec-offline-004"
TTL            = 300
ACTION = "contact.read"


def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.capability_mount.models import CapabilityPackage
    from cappo_backend.config import Settings
    from cappo_backend.services.mount_pgl import AuditPGLAnchor
    
    # Configure production PGL anchor that points to the blocked IP
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
            purpose="G1.4 offline test capability",
            reads=[ACTION],
            writes=[],
        )
    )
    return reg

def test_g1_4_wan_off_cose_evidence_continuity(db: Session):
    # Phase 1: Pre-cut
    parent_token_b64 = mint_biscuit_capability(
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
        capability_id=CAPABILITY_ID,
        reads=[ACTION],
        writes=[],
        execution_id=EXEC_ID,
        ttl_seconds=TTL,
    )
    attenuate_biscuit_capability(
        token_b64=parent_token_b64,
        reads=[ACTION],
        writes=[],
        ttl_seconds=TTL - 30,
    )
    
    reg = _build_registry(db)
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(
            workspace="ws_1",
            project="prj_1",
            reads=[ACTION],
            writes=[]
        ),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=TTL,
        owner_principal="auth-disabled",
        execution_id=EXEC_ID,
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None, f"Mount failed pre-cut: {reason}"
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce

    tree_before = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    size_before = tree_before.size
    tree_before.root()

    # Phase 2: WAN OFF execution 1
    with no_wan():
        decision1, dec_reason1, _anchor1, binding1 = reg.evaluate(
            mount_id=mount_id,
            action=ACTION,
            token_id=token_id,
            nonce=nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd" * 16,
                "trust_domain": "example.org",
            },
        )
        assert decision1 == Decision.ALLOW

    # Verify state after execution 1
    tree_mid = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    size_mid = tree_mid.size
    assert size_mid == size_before + 1
    root_mid = tree_mid.root()

    # Phase 3: WAN OFF execution 2 (new mount to avoid replay block)
    with no_wan():
        mount_record2, _, _ = reg.request_mount(
            package_ref=CAPABILITY_ID,
            scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
            role="agent", policy=MountPolicy(), ttl_seconds=TTL,
            owner_principal="auth-disabled", execution_id=EXEC_ID,
            caller_spiffe_id=CALLER_SPIFFE, executor_spiffe_id=EXECUTOR_SPIFFE,
        )
        decision2, dec_reason2, _anchor2, binding2 = reg.evaluate(
            mount_id=mount_record2.mount.id,
            action=ACTION,
            token_id=mount_record2.token.token_id,
            nonce=mount_record2.token.nonce,
            owner_principal="auth-disabled",
            spiffe_fields={
                "caller_spiffe_id": CALLER_SPIFFE,
                "executor_spiffe_id": EXECUTOR_SPIFFE,
                "caller_cert_sha256": "abcd" * 16,
                "trust_domain": "example.org",
            },
        )
        assert decision2 == Decision.ALLOW

    # Verify state after execution 2
    tree_after = AppendOnlyMerkleTree(get_merkle_ordered_cose_bytes(db))
    size_after = tree_after.size
    assert size_after == size_before + 2
    root_after = tree_after.root()

    # Verify consistency proof locally
    proof = tree_after.consistency_proof(size_mid, size_after)
    assert verify_consistency_proof(size_mid, size_after, root_mid, root_after, proof)

    # EXACTLY_TWO_CONSEQUENCES
    all_receipts = db.query(CapabilityActionReceipt).all()
    receipts = [r for r in all_receipts if r.execution_id == EXEC_ID]
    assert len(receipts) == 2, f"Expected exactly 2 ALLOW receipts, got {len(receipts)}"

    for receipt in receipts:
        assert receipt.signed_receipt_cose is not None
        ev_pub = get_evidence_key_pair().public_key()
        assert verify_signed_execution_evidence(receipt.signed_receipt_cose, ev_pub), "Signed COSE evidence must verify"
        assert receipt.merkle_leaf_index is not None
