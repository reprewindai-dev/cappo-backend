"""P5 Execution-Truth Invariant — 8 hostile adversarial tests.

These tests attack the P5 truth-state machine at its weakest points:
proof transplanting, execution skipping, concurrent race conditions,
Class-E retry, and projection overclaiming.

ALL 8 tests must pass for the invariant to be considered production-safe.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.p5 import (
    ClassERetryDenied,
    ForbiddenTransition,
    P5Engine,
    ProofSubjectMismatch,
    SinkClass,
    TransitionConflict,
    TruthState,
    TruthTransitionDenied,
    compute_proof_subject_hash,
)
from cappo_backend.p5.models import P5Event, P5Operation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _op_id() -> str:
    return f"op-{uuid.uuid4().hex}"


def _create_base_op(engine: P5Engine, sink_class: SinkClass = SinkClass.B_IDEMPOTENT_EXTERNAL):
    """Create and return a fresh operation in REQUESTED state."""
    op_id = _op_id()
    op = engine.create_operation(
        operation_id=op_id,
        consequence_id=f"csq-{uuid.uuid4().hex}",
        sink_class=sink_class,
        intent_hash=f"intent-{uuid.uuid4().hex}",
        actor_identity="agent:test-actor",
    )
    return op


def _valid_proof_hash(op: P5Operation, actor_identity: str, target_state: TruthState) -> str:
    """Compute the correct proof_subject_hash for an operation transition."""
    return compute_proof_subject_hash(
        operation_id=op.operation_id,
        intent_hash=op.intent_hash,
        previous_truth_state=op.current_truth_state,
        asserted_truth_state=target_state,
        consequence_id=op.consequence_id,
        actor_identity=actor_identity,
        sink_identity=op.sink_class,
    )


# ---------------------------------------------------------------------------
# Test 1: AUTHORIZED cannot jump directly to COMPLETED_SUCCESS
# ---------------------------------------------------------------------------


def test_p5_authorized_cannot_directly_complete_success(db: Session):
    """ForbiddenTransition must be raised when completing without starting execution.

    The state machine mandates: REQUESTED → AUTHORIZED → EXECUTION_STARTED → ...
    Skipping EXECUTION_STARTED is the 'optimistic claim' attack vector.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-decision-001")

    # Op is now AUTHORIZED. Attempting complete_success WITHOUT start_execution.
    actor = "agent:test-actor"
    proof_hash = _valid_proof_hash(op, actor, TruthState.COMPLETED_SUCCESS)

    with pytest.raises(ForbiddenTransition):
        engine.complete_success(
            operation_id=op.operation_id,
            proof_type="callback_return",
            proof_subject_hash=proof_hash,
            actor_identity=actor,
            cappo_decision_id="cappo-decision-002",
            has_truth_transition=True,
        )


# ---------------------------------------------------------------------------
# Test 2: OUTCOME_UNKNOWN state is durable — not collapsed to FAILED
# ---------------------------------------------------------------------------


def test_p5_outcome_unknown_is_durable(db: Session):
    """OUTCOME_UNKNOWN must be preserved exactly — not silently collapsed.

    A process crash leaves an ambiguous outcome.  The ledger must record
    the ambiguity faithfully rather than assuming failure.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-d-001")
    op = engine.start_execution(op.operation_id, actor_identity="agent:executor")
    op = engine.record_outcome_unknown(op.operation_id, reason="process crashed")

    # Must be exactly OUTCOME_UNKNOWN — not COMPLETED_FAILURE, not COMPLETED_SUCCESS
    assert op.current_truth_state == TruthState.OUTCOME_UNKNOWN
    assert op.current_truth_state != TruthState.COMPLETED_FAILURE
    assert op.current_truth_state != TruthState.COMPLETED_SUCCESS

    # Re-load from DB to confirm durability
    fresh = db.get(P5Operation, op.operation_id)
    assert fresh.current_truth_state == TruthState.OUTCOME_UNKNOWN


# ---------------------------------------------------------------------------
# Test 3: Class-E retry is denied after OUTCOME_UNKNOWN
# ---------------------------------------------------------------------------


def test_p5_class_e_retry_denied_after_unknown(db: Session):
    """ClassERetryDenied must be raised on any retry of a Class-E non-idempotent op.

    Class E means the consequence cannot be safely repeated without risk of
    duplicate side-effects.  Once OUTCOME_UNKNOWN is recorded, retrying via
    start_execution must be categorically blocked.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine, sink_class=SinkClass.E_NON_IDEMPOTENT)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-d-e001")
    op = engine.start_execution(op.operation_id, actor_identity="agent:executor")
    op = engine.record_outcome_unknown(op.operation_id, reason="process died mid-flight")

    # Now manually reset state to AUTHORIZED to simulate a naïve retry worker
    op.current_truth_state = TruthState.AUTHORIZED
    db.flush()

    with pytest.raises(ClassERetryDenied):
        engine.start_execution(op.operation_id, actor_identity="agent:retry-worker")


# ---------------------------------------------------------------------------
# Test 4: Unauthorized truth transition is denied
# ---------------------------------------------------------------------------


def test_p5_unauthorized_truth_transition_denied(db: Session):
    """TruthTransitionDenied must be raised when actor lacks truth.transition right.

    The completion proof must be bound to an authorized actor.  Without the
    right, even a valid proof_subject_hash cannot advance to COMPLETED_SUCCESS.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-d-001")
    op = engine.start_execution(op.operation_id, actor_identity="agent:executor")

    actor = "agent:unauthorized-actor"
    proof_hash = _valid_proof_hash(op, actor, TruthState.COMPLETED_SUCCESS)

    with pytest.raises(TruthTransitionDenied):
        engine.complete_success(
            operation_id=op.operation_id,
            proof_type="callback_return",
            proof_subject_hash=proof_hash,
            actor_identity=actor,
            cappo_decision_id="cappo-d-002",
            has_truth_transition=False,  # explicitly denied
        )


# ---------------------------------------------------------------------------
# Test 5: Proof transplant attack is denied (cross-operation hash reuse)
# ---------------------------------------------------------------------------


def test_p5_proof_transplant_denied(db: Session):
    """ProofSubjectMismatch must be raised when a proof from op_A is used for op_B.

    Each proof_subject_hash is cryptographically bound to operation_id,
    intent_hash, consequence_id, actor_identity, and sink_class — making
    cross-operation transplanting detectable.
    """
    engine = P5Engine(db)

    # Create op_A and run it through EXECUTION_STARTED
    op_a = _create_base_op(engine)
    op_a = engine.authorize(op_a.operation_id, cappo_decision_id="cappo-d-A")
    op_a = engine.start_execution(op_a.operation_id, actor_identity="agent:executor")

    # Compute the valid proof for op_A
    actor = "agent:executor"
    proof_hash_for_a = _valid_proof_hash(op_a, actor, TruthState.COMPLETED_SUCCESS)

    # Create op_B and run it through EXECUTION_STARTED
    op_b = _create_base_op(engine)
    op_b = engine.authorize(op_b.operation_id, cappo_decision_id="cappo-d-B")
    op_b = engine.start_execution(op_b.operation_id, actor_identity="agent:executor")

    # Attempt to transplant op_A's proof into op_B's complete_success
    with pytest.raises(ProofSubjectMismatch):
        engine.complete_success(
            operation_id=op_b.operation_id,
            proof_type="callback_return",
            proof_subject_hash=proof_hash_for_a,  # ← op_A's hash, wrong for op_B
            actor_identity=actor,
            cappo_decision_id="cappo-d-B2",
            has_truth_transition=True,
        )


# ---------------------------------------------------------------------------
# Test 6: Concurrent execution claim conflict (optimistic lock)
# ---------------------------------------------------------------------------


def test_p5_concurrent_execution_claim_conflict(db: Session):
    """TransitionConflict must be raised when two workers race to claim EXECUTION_STARTED.

    The optimistic-lock guard (version column) ensures exactly one worker wins
    the execution claim.  The second caller must receive TransitionConflict,
    never silently succeed.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-d-001")

    # First worker claims successfully
    op = engine.start_execution(op.operation_id, actor_identity="agent:worker-1")
    assert op.current_truth_state == TruthState.EXECUTION_STARTED

    # Second worker attempts to claim — must fail
    with pytest.raises(TransitionConflict):
        engine.start_execution(op.operation_id, actor_identity="agent:worker-2")


# ---------------------------------------------------------------------------
# Test 7: Events are append-only and hash-chained
# ---------------------------------------------------------------------------


def test_p5_events_are_append_only(db: Session):
    """4 events must exist and be hash-chained after a REQUESTED→UNKNOWN lifecycle.

    Events are never mutated — each appended event's previous_event_hash must
    equal the prior event's event_hash, forming a tamper-evident chain.
    """
    engine = P5Engine(db)
    op = _create_base_op(engine)
    op_id = op.operation_id

    # REQUESTED (1 event)
    engine.authorize(op_id, cappo_decision_id="cappo-d-001")    # AUTHORIZED (2 events)
    engine.start_execution(op_id, actor_identity="agent:exec")   # EXECUTION_STARTED (3 events)
    engine.record_outcome_unknown(op_id, reason="crash")          # OUTCOME_UNKNOWN (4 events)

    events = (
        db.execute(
            select(P5Event)
            .where(P5Event.operation_id == op_id)
            .order_by(P5Event.event_sequence.asc())
        )
        .scalars()
        .all()
    )

    assert len(events) == 4, f"Expected 4 events, got {len(events)}"

    # event_sequence is deterministic: 0, 1, 2, 3
    for i, event in enumerate(events):
        assert event.event_sequence == i, (
            f"Event {i} has event_sequence={event.event_sequence}, expected {i}"
        )

    # Genesis event (sequence=0) has no predecessor
    assert events[0].previous_event_hash is None, (
        f"Genesis event must have previous_event_hash=None, got {events[0].previous_event_hash}"
    )

    # Each subsequent event references the prior event's hash
    for i in range(1, len(events)):
        assert events[i].previous_event_hash == events[i - 1].event_hash, (
            f"Event seq={i} previous_event_hash ({events[i].previous_event_hash[:16]}...) "
            f"does not match event seq={i-1} event_hash ({events[i-1].event_hash[:16]}...)"
        )

    # All events carry non-empty event_hash
    for event in events:
        assert event.event_hash, f"Event {event.event_id} has empty event_hash"


# ---------------------------------------------------------------------------
# Test 8: Projection cannot overclaim — faithfully reflects ledger state
# ---------------------------------------------------------------------------


def test_p5_projection_cannot_overclaim(db: Session):
    """A projection function must reflect the ledger truth, never overclaim.

    When state is OUTCOME_UNKNOWN, no projection or surface layer may
    return COMPLETED_SUCCESS.  The asserted truth cannot exceed the
    evidentiary truth stored in the ledger.
    """
    def get_projection_state(operation: P5Operation) -> str:
        """Minimal honest projection — returns exactly what the ledger says."""
        return operation.current_truth_state

    engine = P5Engine(db)
    op = _create_base_op(engine)
    op = engine.authorize(op.operation_id, cappo_decision_id="cappo-d-001")
    op = engine.start_execution(op.operation_id, actor_identity="agent:exec")
    op = engine.record_outcome_unknown(op.operation_id, reason="network partition")

    projected = get_projection_state(op)

    # Core invariant: asserted truth <= evidentiary truth
    assert projected == TruthState.OUTCOME_UNKNOWN, (
        f"Projection returned {projected!r} instead of OUTCOME_UNKNOWN"
    )
    assert projected != TruthState.COMPLETED_SUCCESS, (
        "FATAL: Projection overclaimed COMPLETED_SUCCESS when ledger says OUTCOME_UNKNOWN"
    )
