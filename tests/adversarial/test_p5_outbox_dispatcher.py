import pytest
from unittest.mock import Mock
from datetime import datetime, timezone

from sqlalchemy import select
from cappo_backend.p5.engine import P5Engine
from cappo_backend.p5.states import SinkClass, TruthState, P5EventType
from cappo_backend.p5.models import P5Operation, P5Event, P5Outbox
from cappo_backend.p5.dispatcher import P5OutboxDispatcher, OutboxTransportError


import uuid

def _op_id() -> str:
    return f"op-{uuid.uuid4().hex}"

def test_o1_1_event_creates_outbox_item_atomically(db):
    """1. Every P5 event creates exactly one outbox item in the same DB transaction."""
    test_operation_id = _op_id()
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    events = db.execute(select(P5Event).where(P5Event.operation_id == test_operation_id)).scalars().all()
    outbox_items = db.execute(select(P5Outbox)).scalars().all()
    
    assert len(events) == 1
    assert len(outbox_items) == 1
    
    event = events[0]
    outbox = outbox_items[0]
    
    assert outbox.event_id == event.event_id
    assert outbox.target == "PGL"
    assert outbox.status == "PENDING"
    assert outbox.attempts == 0
    assert outbox.payload_hash is not None


def test_o1_2_dispatcher_sends_pending_item(db):
    """2. Dispatcher processes PENDING items in deterministic order."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )

    transport_mock = Mock()
    dispatcher = P5OutboxDispatcher(db, transport_mock)
    
    processed = dispatcher.process_pending()
    assert processed == 1
    assert transport_mock.call_count == 1
    
    # 3. Successful dispatch marks item as SENT with sent_at timestamp.
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "SENT"
    assert outbox.sent_at is not None
    assert outbox.attempts == 1


def test_o1_3_retry_after_transient_failure(db):
    """3. transient failure and retry mechanics"""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )

    transport_mock = Mock(side_effect=[OutboxTransportError("timeout"), None])
    dispatcher = P5OutboxDispatcher(db, transport_mock)
    
    # First pass: fails
    processed = dispatcher.process_pending()
    assert processed == 0
    assert transport_mock.call_count == 1
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "FAILED_RETRYABLE"
    assert outbox.attempts == 1
    assert outbox.last_error == "timeout"
    assert outbox.sent_at is None
    
    # Second pass: succeeds
    processed = dispatcher.process_pending()
    assert processed == 1
    assert transport_mock.call_count == 2
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "SENT"
    assert outbox.attempts == 2
    assert outbox.sent_at is not None


def test_o1_4_no_duplicate_dispatch_on_retry(db):
    """4. Retry does not duplicate ledger/evidence commits. 
    (Simulated by successful items not being picked up by process_pending)."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )

    transport_mock = Mock()
    dispatcher = P5OutboxDispatcher(db, transport_mock)
    
    processed = dispatcher.process_pending()
    assert processed == 1
    
    # Try again
    processed = dispatcher.process_pending()
    assert processed == 0
    assert transport_mock.call_count == 1


def test_o1_5_crash_before_dispatch_preserves_pending(db):
    """8. Crash before dispatch preserves PENDING state."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "PENDING"
    assert outbox.attempts == 0
    
    def crashing_transport(payload):
        raise RuntimeError("Crash before actual network send")
        
    dispatcher = P5OutboxDispatcher(db, crashing_transport)
    dispatcher.process_pending()
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "FAILED_RETRYABLE"
    assert outbox.attempts == 1


def test_o1_6_crash_after_dispatch_is_idempotently_recovered(db):
    """7. Crash after dispatch but before SENT does not create duplicate consequence."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    call_count = [0]
    
    def simulate_idempotent_transport(payload):
        call_count[0] += 1
        if call_count[0] == 1:
            # First time it hits PGL, but we crash locally before saving SENT
            raise RuntimeError("Crashed after PGL ACK but before DB commit")
        # Second time it hits PGL, PGL drops it due to payload_hash idempotency and returns 200 OK.
        pass

    dispatcher = P5OutboxDispatcher(db, simulate_idempotent_transport)
    dispatcher.process_pending()
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "FAILED_RETRYABLE"
    
    # Process again
    dispatcher.process_pending()
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "SENT"
    assert call_count[0] == 2


def test_o1_7_poison_item_dead_letters(db):
    """9. Poison item moves to DEAD_LETTER after max attempts."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    transport_mock = Mock(side_effect=OutboxTransportError("permanent failure"))
    dispatcher = P5OutboxDispatcher(db, transport_mock, max_attempts=3)
    
    # Attempt 1
    dispatcher.process_pending()
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "FAILED_RETRYABLE"
    assert outbox.attempts == 1
    
    # Attempt 2
    dispatcher.process_pending()
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "FAILED_RETRYABLE"
    assert outbox.attempts == 2
    
    # Attempt 3 (hits max)
    dispatcher.process_pending()
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "DEAD_LETTER"
    assert outbox.attempts == 3
    assert outbox.dead_lettered_at is not None


def test_o1_8_truth_state_unchanged_when_dispatch_fails(db):
    """10. P5 truth state remains unchanged by outbox transport failure."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    op = db.get(P5Operation, test_operation_id)
    assert op.current_truth_state == TruthState.REQUESTED
    
    transport_mock = Mock(side_effect=OutboxTransportError("fail"))
    dispatcher = P5OutboxDispatcher(db, transport_mock)
    dispatcher.process_pending()
    
    op_after = db.get(P5Operation, test_operation_id)
    assert op_after.current_truth_state == TruthState.REQUESTED


def test_o1_9_sent_outbox_does_not_imply_completed_success(db):
    """12. Projection cannot display SENT as COMPLETED_SUCCESS."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    transport_mock = Mock()
    dispatcher = P5OutboxDispatcher(db, transport_mock)
    dispatcher.process_pending()
    
    outbox = db.execute(select(P5Outbox)).scalars().one()
    assert outbox.status == "SENT"
    
    op = db.get(P5Operation, test_operation_id)
    assert op.current_truth_state == TruthState.REQUESTED
    assert op.current_truth_state != TruthState.COMPLETED_SUCCESS


def test_o1_10_deterministic_ordering_by_event_sequence(db):
    """4. Dispatcher must process events in deterministic order: event_sequence ASC."""
    test_operation_id = _op_id()
    engine = P5Engine(db)
    engine.create_operation(
        operation_id=test_operation_id,
        consequence_id="test_conseq",
        sink_class=SinkClass.B_IDEMPOTENT_EXTERNAL,
        intent_hash="ihash123",
        actor_identity="test_actor",
    )
    
    # Manually append some more events
    engine.authorize(test_operation_id, "test_actor")
    engine.start_execution(test_operation_id, actor_identity="test_actor")
    
    outboxes = db.execute(select(P5Outbox).order_by(P5Outbox.created_at)).scalars().all()
    assert len(outboxes) == 3
    
    # We scramble their created_at timestamps maliciously to prove that 
    # event_sequence correctly orders them in the dispatcher.
    # Note: we need to scramble P5Outbox items.
    outboxes[0].created_at = datetime(2050, 1, 1, tzinfo=timezone.utc)
    outboxes[1].created_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    outboxes[2].created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db.commit()
    
    received_sequences = []
    def recording_transport(payload):
        # We need to find the event_sequence from the DB to see if it was ordered correctly
        event = db.get(P5Event, payload["event_id"])
        received_sequences.append(event.event_sequence)
        
    dispatcher = P5OutboxDispatcher(db, recording_transport)
    dispatcher.process_pending(limit=3)
    
    # Even though timestamps were 2050, 2000, 2020, they should be processed in order 0, 1, 2
    # due to the JOIN with P5Event and order_by(P5Event.event_sequence.asc())
    assert received_sequences == [0, 1, 2]
