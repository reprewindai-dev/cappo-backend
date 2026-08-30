"""
Adversarial test suite for P5 — Truth-State Synchronization.

Validates that Veklom never claims a consequence occurred unless it possesses
cryptographic/durable proof, and never writes blind completion records when
process boundaries crash.
"""


import pytest
from sqlalchemy import select

from cappo_backend.capability_mount.engine import PolicyError
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceState,
)


class DummyException(Exception):
    pass


class CappoUncertainError(Exception):
    """Signals that an error occurred but the consequence may have happened."""


from sqlalchemy.orm import Session

from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy, MountScope
from cappo_backend.capability_mount.service import MountRegistry


def _setup_binding(db: Session, rules: list[dict]):
    from cappo_backend.db.base import Base
    Base.metadata.create_all(bind=db.get_bind())
    
    # Simple dummy anchor
    class ConfirmedAnchor:
        def anchor(self, *args, **kwargs):
            from cappo_backend.capability_mount.service import AnchorResult
            return AnchorResult("confirmed", "fake-receipt")

    reg = MountRegistry(db, anchor=ConfirmedAnchor())
    
    # We will just map rules to writes for the package
    writes = [r["action"] for r in rules]
    
    pkg = CapabilityPackage(
        id="p5.pkg@v1",
        family="test",
        title="P5 Package",
        purpose="Truth state testing",
        reads=[],
        writes=writes,
    )
    reg.register_package(pkg)
    
    mount_record, anchor, reason = reg.request_mount(
        package_ref="p5.pkg@v1",
        scope=MountScope(workspace="ws", project="proj", reads=[], writes=writes),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=600,
        owner_principal="test_owner",
        execution_id="exec-1",
        caller_spiffe_id="spiffe://cappo/caller",
        executor_spiffe_id="spiffe://cappo/executor",
    )
    assert mount_record is not None, f"Mount failed: {reason}"
    
    # Get the binding by fetching the DB row
    from sqlalchemy import select

    from cappo_backend.models.capability_mount import CapabilityMount
    row = db.execute(
        select(CapabilityMount).where(CapabilityMount.mount_id == mount_record.mount.id)
    ).scalar_one()
    binding = reg._record(row).binding
    return binding, reg


def test_p5_1_authorize_without_execution(db: Session):
    """
    Test: authorize then callback never called
    Required: AUTHORIZED, not SUCCEEDED
    """
    binding, reg = _setup_binding(db, [{"action": "test_action", "effect": "allow"}])
    
    # We call internal _local_eval and the evaluator just to write the AUTHORIZED event
    # similar to what would happen if the process crashed between evaluate and begin_consequence.
    binding._cappo_evaluator("test_action", {}, operation_id="test-op", intent_hash="test-intent")
    
    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "test-op")
    ).scalars().all()
    
    assert len(events) == 1
    assert events[0].state == ConsequenceState.AUTHORIZED.value

def test_p5_2_callback_raises_before_side_effect(db: Session):
    """
    Test: callback raises before side effect -> FAILED
    """
    binding, reg = _setup_binding(db, [{"action": "write_db", "effect": "allow"}])

    def failing_action():
        raise DummyException("Failed before DB write")

    with pytest.raises(DummyException):
        binding.consequence("write_db", failing_action, operation_id="fail-op-1")

    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "fail-op-1")
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()

    assert len(events) == 3
    assert events[0].state == ConsequenceState.AUTHORIZED.value
    assert events[1].state == ConsequenceState.STARTED.value
    assert events[2].state == ConsequenceState.FAILED.value
    assert events[2].completion_proof_type == "callback_exception"


def test_p5_3_side_effect_occurs_then_callback_raises(db: Session):
    """
    Test: side effect occurs then callback raises -> UNKNOWN
    """
    binding, reg = _setup_binding(db, [{"action": "write_db", "effect": "allow"}])

    def ambiguous_action():
        raise CappoUncertainError("Uncertain outcome")

    with pytest.raises(CappoUncertainError):
        binding.consequence("write_db", ambiguous_action, operation_id="ambiguous-op-1")
        
    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "ambiguous-op-1")
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()

    assert len(events) == 3
    assert events[2].state == ConsequenceState.OUTCOME_UNKNOWN.value


def test_p5_4_process_killed_before_receipt(db: Session):
    """
    Test: side effect succeeds then process killed before receipt -> UNKNOWN.
    Simulated by stripping out the completion reporter so the final event is never written.
    """
    binding, reg = _setup_binding(db, [{"action": "write_db", "effect": "allow"}])
    
    # Strip completion reporter to simulate hard crash
    binding._completion_reporter = None

    def success_action():
        return "did it"

    res = binding.consequence("write_db", success_action, operation_id="crash-op-1")
    assert res == "did it"

    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == "crash-op-1")
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()

    # Should only have AUTHORIZED and STARTED
    assert len(events) == 2
    assert events[0].state == ConsequenceState.AUTHORIZED.value
    assert events[1].state == ConsequenceState.STARTED.value
    # No completion event!


def test_p5_5_idempotency_mismatch_and_replay(db: Session):
    """
    Test: duplicate same operation_id -> one consequence
    Test: same operation_id, changed intent_hash -> DENY (IDEMPOTENCY_INTENT_MISMATCH)
    """
    binding, reg = _setup_binding(db, [{"action": "transfer", "effect": "allow"}])

    op_id = "op-12345"
    
    def action1(amount): return f"sent {amount}"
    def action2(amount): return f"sent {amount}"

    res = binding.consequence("transfer", action1, operation_id=op_id, amount=100)
    assert res == "sent 100"

    # Now retry with same op_id but different intent (amount changed)
    with pytest.raises(PolicyError, match="idempotency_intent_mismatch"):
        binding.consequence("transfer", action2, operation_id=op_id, amount=200)

    # Retry with identical intent should be rejected as already complete
    with pytest.raises(PolicyError, match="idempotency_replay:succeeded"):
        binding.consequence("transfer", action1, operation_id=op_id, amount=100)


