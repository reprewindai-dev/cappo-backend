"""
Regression Test: MountRegistry.evaluate assigns Merkle leaf index.

Ensures that calling `reg.evaluate()` successfully in the production code
automatically assigns a monotonically ascending `merkle_leaf_index` to the
resulting CapabilityActionReceipt before committing the transaction, without
test-side manual intervention.
"""

from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy, MountScope
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.config import Settings
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.services.mount_pgl import AuditPGLAnchor
from tests.test_g0b3_biscuit_issuance import mint_biscuit_capability


def test_evaluate_assigns_merkle_leaf_index(db: Session):
    CAPABILITY_ID = "regression.echo@v1"
    ACTION = "contact.read"
    EXEC_ID = "test-exec-123"

    settings = Settings(pgl_ledger_url="http://127.0.0.1:0", pgl_ledger_timeout_ms=10) # Fails fast
    reg = MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

    reg.register_package(
        CapabilityPackage(
            id=CAPABILITY_ID,
            family="test.echo",
            title="Echo",
            purpose="Regression test",
            reads=[ACTION],
            writes=[],
        )
    )

    mint_biscuit_capability(
        caller_spiffe_id="spiffe://example.org/workload/regression",
        executor_spiffe_id="spiffe://example.org/workload/cappo-backend",
        capability_id=CAPABILITY_ID,
        reads=[ACTION],
        writes=[],
        execution_id=EXEC_ID,
        ttl_seconds=300,
    )

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
        ttl_seconds=300,
        owner_principal="auth-disabled",
        execution_id=EXEC_ID,
        caller_spiffe_id="spiffe://example.org/workload/regression",
        executor_spiffe_id="spiffe://example.org/workload/cappo-backend",
    )
    assert mount_record is not None, "Mount failed"

    # Evaluate (which should now automatically assign the merkle_leaf_index)
    decision, dec_reason, _anchor, binding = reg.evaluate(
        mount_id=mount_record.mount.id,
        action=ACTION,
        token_id=mount_record.token.token_id,
        nonce=mount_record.token.nonce,
        owner_principal="auth-disabled",
        spiffe_fields={
            "caller_spiffe_id": "spiffe://example.org/workload/regression",
            "executor_spiffe_id": "spiffe://example.org/workload/cappo-backend",
            "caller_cert_sha256": "abcd" * 16,
            "trust_domain": "example.org",
        },
    )

    assert decision.value == "allow", f"Evaluate failed: {dec_reason}"

    # Verify receipt was created with an index
    all_receipts = db.query(CapabilityActionReceipt).filter_by(execution_id=EXEC_ID).all()
    assert len(all_receipts) == 1
    receipt = all_receipts[0]

    assert receipt.decision.lower() == "allow"
    assert receipt.merkle_leaf_index is not None, "evaluate() failed to assign a merkle_leaf_index!"
    
    # SPIFFE Evidence Binding check (from G1.1 instructions)
    assert receipt.caller_spiffe_id == "spiffe://example.org/workload/regression"
    assert receipt.executor_spiffe_id == "spiffe://example.org/workload/cappo-backend"
