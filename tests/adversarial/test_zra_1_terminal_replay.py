"""
ZRA-1 — Zero Residual Agency / Terminal Replay

Hypothesis: The architecture requires that execution-specific authority and ephemeral 
state become unusable at terminal state. Zero Residual Agency (ZRA-1) is a constitutional 
requirement, not a proven property. Current implementation invalidates the Ephemeral 
Execution Identity on completion; full ZRA-1 remains NOT PROVEN until adversarial tests 
demonstrate that post-terminal credentials, sockets, mounts, secrets, processes, and 
memory residues cannot be recovered or reused.
"""

from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import Decision, MountPolicy, MountScope
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.services.mount_pgl import AuditPGLAnchor

CALLER_SPIFFE  = "spiffe://example.org/workload/cappo-backend"
EXECUTOR_SPIFFE = "spiffe://example.org/workload/zra1-agent"
CAPABILITY_ID  = "test.resource@v1"
ACTION         = "resource.read"

def _build_registry(db: Session) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_zra_1_terminal_credential_replay(db: Session):
    """
    Attempt to reuse an EEI token after the task has reached terminal state.
    """
    reg = _build_registry(db)
    
    # 0. Register package
    from cappo_backend.capability_mount.models import CapabilityPackage
    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo Capability",
            purpose="ZRA-1 test capability",
            reads=[ACTION],
            writes=[],
        )
    )

    # 1. Setup a valid mount (as if the task is running)
    mount_record, anchor, reason = reg.request_mount(
        package_ref=CAPABILITY_ID,
        scope=MountScope(workspace="ws_1", project="prj_1", reads=[ACTION], writes=[]),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id="zra1-exec-001",
        caller_spiffe_id=CALLER_SPIFFE,
        executor_spiffe_id=EXECUTOR_SPIFFE,
    )
    assert mount_record is not None
    mount_id = mount_record.mount.id
    token_id = mount_record.token.token_id
    nonce = mount_record.token.nonce

    # 2. Simulate task terminal state (e.g., capability revoked, or execution completed)
    from cappo_backend.capability_mount.models import UnmountReason
    reg.terminate(mount_id=mount_id, reason=UnmountReason.TOKEN_EXPIRY)

    # 3. Attacker attempts to replay the credentials to gain access
    decision, reason, _, _ = reg.evaluate(
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

    # Falsifier: If decision == ALLOW, ZRA is broken.
    assert decision != Decision.ALLOW, "ZRA-1 Falsified: Post-terminal credentials successfully replayed."
