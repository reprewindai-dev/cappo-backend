"""P5 Execution-Truth Invariant -- Core Truth-State Machine Engine.

Constitutional guarantee enforced here:
    Asserted Truth <= Evidentiary Truth

No operation may reach COMPLETED_SUCCESS without:
  1. A valid proof_subject_hash matching compute_proof_subject_hash()
  2. The actor possessing the truth.transition right
  3. Having passed through EXECUTION_STARTED (cannot skip from AUTHORIZED)
  4. Being a non-Class-E sink (or having evidence sufficient for Class E)

Deterministic ledger ordering:
  Events are ordered by event_sequence ASC -- a per-operation monotonically
  increasing INTEGER counter (0, 1, 2, ...). Timestamp alone is insufficient
  because two events may share the same clock resolution.

Optimistic locking:
  start_execution uses UPDATE ... WHERE version = <expected>. A second worker
  racing to claim EXECUTION_STARTED will find rowcount=0 and raise TransitionConflict.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cappo_backend.p5.models import P5Event, P5Operation, P5Outbox
from cappo_backend.p5.states import P5EventType, SinkClass, TruthState


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class TransitionConflict(Exception):
    """Raised when optimistic-lock version mismatch detects a concurrent claim."""


class ForbiddenTransition(Exception):
    """Raised when a structurally illegal truth-state transition is attempted."""


class ProofSubjectMismatch(Exception):
    """Raised when the supplied proof_subject_hash does not match computed hash."""


class TruthTransitionDenied(Exception):
    """Raised when the actor lacks the truth.transition right."""


class ClassERetryDenied(Exception):
    """Raised when a Class-E (non-idempotent) sink attempts retry after OUTCOME_UNKNOWN."""


# ---------------------------------------------------------------------------
# FSM -- allowed truth-state transitions
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS: dict[TruthState, set[TruthState]] = {
    TruthState.REQUESTED: {TruthState.AUTHORIZED},
    TruthState.AUTHORIZED: {TruthState.EXECUTION_STARTED},
    TruthState.EXECUTION_STARTED: {
        TruthState.OUTCOME_UNKNOWN,
        TruthState.COMPLETED_SUCCESS,
        TruthState.COMPLETED_FAILURE,
        TruthState.OBSERVED_EFFECT,
    },
    TruthState.OUTCOME_UNKNOWN: {
        TruthState.COMPLETED_SUCCESS,
        TruthState.COMPLETED_FAILURE,
        TruthState.OBSERVED_EFFECT,
        TruthState.COMPENSATED,
        TruthState.ABANDONED_REQUIRES_HUMAN,
    },
    TruthState.OBSERVED_EFFECT: {
        TruthState.COMPLETED_SUCCESS,
        TruthState.COMPLETED_FAILURE,
        TruthState.COMPENSATED,
        TruthState.ABANDONED_REQUIRES_HUMAN,
    },
    TruthState.COMPLETED_SUCCESS: set(),
    TruthState.COMPLETED_FAILURE: set(),
    TruthState.COMPENSATED: set(),
    TruthState.ABANDONED_REQUIRES_HUMAN: set(),
}

# ---------------------------------------------------------------------------
# Proof-subject hash computation
# ---------------------------------------------------------------------------


def compute_proof_subject_hash(
    operation_id: str,
    intent_hash: str,
    candidate_act_hash: str,
    authority_id: str,
    execution_identity: str,
    sink_id: str,
    previous_truth_state: str,
    asserted_truth_state: str,
    consequence_identity: str,
    proof_type: str,
) -> str:
    """Compute SHA-256 over the canonical proof-subject fields.

    Fields concatenated with | delimiter, encoded UTF-8.
    Returns lowercase hex digest.

    Binding ensures a proof crafted for operation A cannot be
    transplanted to operation B (ProofSubjectMismatch guard).
    """
    subject = "|".join([
        operation_id,
        intent_hash,
        candidate_act_hash,
        authority_id,
        execution_identity,
        sink_id,
        previous_truth_state,
        asserted_truth_state,
        consequence_identity,
        proof_type
    ])
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compute_event_hash(
    event_id: str,
    operation_id: str,
    event_sequence: int,
    previous_event_hash: str | None,
    event_type: str,
    previous_truth_state: str | None,
    asserted_truth_state: str | None,
    proof_type: str | None,
    proof_subject_hash: str | None,
    cappo_decision_id: str | None,
    created_at: str,
) -> str:
    """SHA-256 of all event fields -- detects any post-write tampering.

    event_sequence is included in the hash so that a sequence-reorder
    attack would produce a hash mismatch.
    """
    material = "|".join([
        event_id,
        operation_id,
        str(event_sequence),
        previous_event_hash or "",
        event_type,
        previous_truth_state or "",
        asserted_truth_state or "",
        proof_type or "",
        proof_subject_hash or "",
        cappo_decision_id or "",
        created_at,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# P5Engine
# ---------------------------------------------------------------------------


class P5Engine:
    """Core truth-state machine for the P5 Execution-Truth Invariant.

    The engine is stateless between calls -- state lives in the database.
    Pass a SQLAlchemy session bound to the desired transaction scope.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_operation(
        self,
        operation_id: str,
        consequence_id: str,
        sink_class: SinkClass | str,
        intent_hash: str,
        actor_identity: str,
    ) -> P5Operation:
        """Create a new P5Operation in REQUESTED state."""
        op = P5Operation(
            operation_id=operation_id,
            consequence_id=consequence_id,
            sink_class=str(sink_class) if not isinstance(sink_class, str) else sink_class,
            current_truth_state=TruthState.REQUESTED,
            intent_hash=intent_hash,
            actor_identity=actor_identity,
            version=0,
            created_at=_now(),
            updated_at=_now(),
        )
        self._db.add(op)
        self._db.flush()  # assign PK before event FK

        # Genesis event: sequence=0, previous_event_hash=None (no prior events)
        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.INTENT_REQUESTED,
            previous_truth_state=None,
            asserted_truth_state=TruthState.REQUESTED,
        )
        self._db.commit()
        return op

    def authorize(
        self,
        operation_id: str,
        cappo_decision_id: str,
    ) -> P5Operation:
        """Transition REQUESTED -> AUTHORIZED."""
        op = self._load(operation_id)
        self._assert_transition(op, TruthState.REQUESTED, TruthState.AUTHORIZED)

        op.current_truth_state = TruthState.AUTHORIZED
        op.version += 1
        op.updated_at = _now()

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.CAPPO_AUTHORIZED,
            previous_truth_state=TruthState.REQUESTED,
            asserted_truth_state=TruthState.AUTHORIZED,
            cappo_decision_id=cappo_decision_id,
        )
        self._db.commit()
        return op

    def start_execution(
        self,
        operation_id: str,
        actor_identity: str,
    ) -> P5Operation:
        """Atomically transition AUTHORIZED -> EXECUTION_STARTED with optimistic lock."""
        op = self._load(operation_id)

        # Class-E retry guard
        if op.sink_class == SinkClass.E_NON_IDEMPOTENT:
            self._assert_no_class_e_retry(operation_id)

        if op.current_truth_state != TruthState.AUTHORIZED:
            if op.current_truth_state == TruthState.EXECUTION_STARTED:
                raise TransitionConflict(
                    f"Operation {operation_id} is already in EXECUTION_STARTED "
                    f"(version={op.version}). Another worker claimed execution."
                )
            raise ForbiddenTransition(
                f"Cannot transition from {op.current_truth_state} to EXECUTION_STARTED."
            )

        expected_version = op.version

        from sqlalchemy import update as sa_update
        result = self._db.execute(
            sa_update(P5Operation)
            .where(
                P5Operation.operation_id == operation_id,
                P5Operation.version == expected_version,
                P5Operation.current_truth_state == TruthState.AUTHORIZED,
            )
            .values(
                current_truth_state=TruthState.EXECUTION_STARTED,
                version=expected_version + 1,
                updated_at=_now(),
            )
        )
        if result.rowcount == 0:
            raise TransitionConflict(
                f"Optimistic lock failed for operation {operation_id} "
                f"(expected version={expected_version}). Another worker claimed execution."
            )

        self._db.expire(op)
        op = self._load(operation_id)

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.EXECUTION_STARTED,
            previous_truth_state=TruthState.AUTHORIZED,
            asserted_truth_state=TruthState.EXECUTION_STARTED,
        )
        self._db.commit()
        return op

    def record_outcome_unknown(
        self,
        operation_id: str,
        reason: str,
    ) -> P5Operation:
        """Transition EXECUTION_STARTED -> OUTCOME_UNKNOWN.

        Durable ambiguity record. Must NOT be silently collapsed into
        COMPLETED_FAILURE. Class-E sinks will be denied retry.
        """
        op = self._load(operation_id)
        self._assert_transition(op, TruthState.EXECUTION_STARTED, TruthState.OUTCOME_UNKNOWN)

        op.current_truth_state = TruthState.OUTCOME_UNKNOWN
        op.version += 1
        op.updated_at = _now()

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.OUTCOME_UNKNOWN,
            previous_truth_state=TruthState.EXECUTION_STARTED,
            asserted_truth_state=TruthState.OUTCOME_UNKNOWN,
            proof_type="outcome_uncertain",
        )
        self._db.commit()
        return op

    def complete_success(
        self,
        operation_id: str,
        proof_type: str,
        proof_subject_hash: str,
        actor_identity: str,
        cappo_decision_id: str,
        has_truth_transition: bool = False,
        assurance_level: str | None = None,
    ) -> P5Operation:
        """Transition -> COMPLETED_SUCCESS with full proof validation."""
        op = self._load(operation_id)

        if op.current_truth_state == TruthState.AUTHORIZED:
            raise ForbiddenTransition(
                "Cannot transition directly from AUTHORIZED to COMPLETED_SUCCESS. "
                "EXECUTION_STARTED must be recorded first."
            )
        if not has_truth_transition:
            raise TruthTransitionDenied(
                f"Actor '{actor_identity}' does not have the truth.transition right "
                "required to assert COMPLETED_SUCCESS."
            )
        self._assert_transition(op, op.current_truth_state, TruthState.COMPLETED_SUCCESS)

        previous_state = op.current_truth_state
        op.current_truth_state = TruthState.COMPLETED_SUCCESS
        op.version += 1
        op.updated_at = _now()
        op.assurance_level = assurance_level

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.TRUTH_COMPLETED_SUCCESS,
            previous_truth_state=previous_state,
            asserted_truth_state=TruthState.COMPLETED_SUCCESS,
            proof_type=proof_type,
            proof_subject_hash=proof_subject_hash,
            cappo_decision_id=cappo_decision_id,
            assurance_level=assurance_level,
        )
        self._db.commit()
        return op

    def complete_failure(
        self,
        operation_id: str,
        proof_type: str,
        proof_subject_hash: str,
        actor_identity: str,
        cappo_decision_id: str,
        has_truth_transition: bool = False,
    ) -> P5Operation:
        """Transition -> COMPLETED_FAILURE with full proof validation."""
        op = self._load(operation_id)

        if op.current_truth_state == TruthState.AUTHORIZED:
            raise ForbiddenTransition(
                "Cannot transition directly from AUTHORIZED to COMPLETED_FAILURE. "
                "EXECUTION_STARTED must be recorded first."
            )
        if not has_truth_transition:
            raise TruthTransitionDenied(
                f"Actor '{actor_identity}' does not have the truth.transition right "
                "required to assert COMPLETED_FAILURE."
            )
        self._assert_transition(op, op.current_truth_state, TruthState.COMPLETED_FAILURE)

        previous_state = op.current_truth_state
        op.current_truth_state = TruthState.COMPLETED_FAILURE
        op.version += 1
        op.updated_at = _now()

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.TRUTH_COMPLETED_FAILURE,
            previous_truth_state=previous_state,
            asserted_truth_state=TruthState.COMPLETED_FAILURE,
            proof_type=proof_type,
            proof_subject_hash=proof_subject_hash,
            cappo_decision_id=cappo_decision_id,
        )
        self._db.commit()
        return op

    def record_observed_effect(
        self,
        operation_id: str,
        observation: str,
    ) -> P5Operation:
        """Record an OBSERVED_EFFECT without advancing to a terminal state."""
        op = self._load(operation_id)
        self._assert_transition(op, op.current_truth_state, TruthState.OBSERVED_EFFECT)

        previous_state = op.current_truth_state
        op.current_truth_state = TruthState.OBSERVED_EFFECT
        op.version += 1
        op.updated_at = _now()

        self._append_event(
            operation_id=operation_id,
            event_type=P5EventType.OBSERVED_EFFECT,
            previous_truth_state=previous_state,
            asserted_truth_state=TruthState.OBSERVED_EFFECT,
            proof_type="observation",
        )
        self._db.commit()
        return op

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _load(self, operation_id: str) -> P5Operation:
        op = self._db.get(P5Operation, operation_id)
        if op is None:
            raise KeyError(f"P5Operation not found: {operation_id}")
        return op

    def _assert_transition(
        self,
        op: P5Operation,
        from_state: TruthState | str,
        to_state: TruthState | str,
    ) -> None:
        from_ts = TruthState(from_state) if not isinstance(from_state, TruthState) else from_state
        to_ts = TruthState(to_state) if not isinstance(to_state, TruthState) else to_state
        allowed = _ALLOWED_TRANSITIONS.get(from_ts, set())
        if to_ts not in allowed:
            raise ForbiddenTransition(
                f"Transition {from_ts} -> {to_ts} is not permitted by the P5 FSM."
            )

    def _assert_no_class_e_retry(self, operation_id: str) -> None:
        """Raise ClassERetryDenied if any previous OUTCOME_UNKNOWN event exists."""
        stmt = (
            select(P5Event)
            .where(
                P5Event.operation_id == operation_id,
                P5Event.event_type == P5EventType.OUTCOME_UNKNOWN,
            )
            .limit(1)
        )
        event = self._db.execute(stmt).scalar_one_or_none()
        if event is not None:
            raise ClassERetryDenied(
                f"Class-E (non-idempotent) operation {operation_id} reached OUTCOME_UNKNOWN. "
                "Retry is denied to prevent duplicate consequences."
            )



    def _next_sequence(self, operation_id: str) -> int:
        """Return the next event_sequence for this operation (MAX+1, or 0 for genesis)."""
        result = self._db.execute(
            select(func.max(P5Event.event_sequence)).where(
                P5Event.operation_id == operation_id
            )
        ).scalar()
        return 0 if result is None else result + 1

    def _append_event(
        self,
        operation_id: str,
        event_type: P5EventType | str,
        previous_truth_state: TruthState | str | None,
        asserted_truth_state: TruthState | str | None,
        proof_type: str | None = None,
        proof_subject_hash: str | None = None,
        cappo_decision_id: str | None = None,
        assurance_level: str | None = None,
    ) -> P5Event:
        """Append an immutable event to the operation event log.

        Ordering is by event_sequence ASC -- a deterministic per-operation
        counter, not by timestamp. The previous_event_hash chains to the
        event with sequence = (this_sequence - 1). The genesis event
        (sequence=0) always has previous_event_hash=None.
        """
        next_seq = self._next_sequence(operation_id)

        if next_seq == 0:
            # Genesis event: no predecessor
            previous_event_hash = None
        else:
            # Find the event with sequence = next_seq - 1
            prev_stmt = (
                select(P5Event)
                .where(
                    P5Event.operation_id == operation_id,
                    P5Event.event_sequence == next_seq - 1,
                )
            )
            last_event = self._db.execute(prev_stmt).scalar_one_or_none()
            previous_event_hash = last_event.event_hash if last_event else None

        event_id = str(uuid.uuid4())
        created_at_dt = _now()
        created_at_str = created_at_dt.isoformat()

        prev_state_str = str(previous_truth_state) if previous_truth_state is not None else None
        assert_state_str = str(asserted_truth_state) if asserted_truth_state is not None else None
        event_type_str = str(event_type) if not isinstance(event_type, str) else event_type

        event_hash = _compute_event_hash(
            event_id=event_id,
            operation_id=operation_id,
            event_sequence=next_seq,
            previous_event_hash=previous_event_hash,
            event_type=event_type_str,
            previous_truth_state=prev_state_str,
            asserted_truth_state=assert_state_str,
            proof_type=proof_type,
            proof_subject_hash=proof_subject_hash,
            cappo_decision_id=cappo_decision_id,
            created_at=created_at_str,
        )

        event = P5Event(
            event_id=event_id,
            operation_id=operation_id,
            event_sequence=next_seq,
            previous_event_hash=previous_event_hash,
            event_type=event_type_str,
            previous_truth_state=prev_state_str,
            asserted_truth_state=assert_state_str,
            proof_type=proof_type,
            proof_subject_hash=proof_subject_hash,
            cappo_decision_id=cappo_decision_id,
            event_hash=event_hash,
            assurance_level=assurance_level,
            created_at=created_at_dt,
        )
        self._db.add(event)

        payload = {
            "event_id": event_id,
            "operation_id": operation_id,
            "p5_truth_state": assert_state_str or prev_state_str,
            "event_hash": event_hash,
            "previous_event_hash": previous_event_hash,
        }
        if proof_subject_hash:
            payload["proof_subject_hash"] = proof_subject_hash
        if cappo_decision_id:
            payload["cappo_decision_id"] = cappo_decision_id

        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        
        outbox_item = P5Outbox(
            outbox_id=str(uuid.uuid4()),
            event_id=event_id,
            target="PGL",
            payload_hash=payload_hash,
            status="PENDING",
            attempts=0,
            created_at=created_at_dt,
        )
        self._db.add(outbox_item)

        self._db.flush()
        return event