import logging
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cappo_backend.p5.models import P5Event, P5Outbox

logger = logging.getLogger(__name__)

def _now() -> datetime:
    return datetime.now(timezone.utc)


class OutboxTransportError(Exception):
    """Raised when the outbox fails to dispatch the payload."""


class P5OutboxDispatcher:
    """Propagates P5 truth-state evidence deterministically to downstream systems (PGL)."""

    def __init__(
        self,
        db: Session,
        transport_fn: Callable[[dict[str, Any]], None],
        max_attempts: int = 5,
    ):
        self._db = db
        self._transport_fn = transport_fn
        self._max_attempts = max_attempts

    def process_pending(self, limit: int = 50) -> int:
        """
        Process PENDING and FAILED_RETRYABLE items.
        Returns the number of items successfully processed.
        """
        # We need a deterministic ordering to avoid deadlocks and ensure chronological
        # propagation. Order by event_sequence ASC, created_at ASC, outbox_id ASC.
        stmt = (
            select(P5Outbox, P5Event)
            .join(P5Event, P5Outbox.event_id == P5Event.event_id)
            .where(P5Outbox.status.in_(["PENDING", "FAILED_RETRYABLE"]))
            .order_by(
                P5Event.event_sequence.asc(),
                P5Outbox.created_at.asc(),
                P5Outbox.outbox_id.asc(),
            )
            .limit(limit)
            # Use with_for_update(skip_locked=True) to avoid blocking other workers
            .with_for_update(skip_locked=True, of=P5Outbox)
        )
        
        results = self._db.execute(stmt).all()
        
        processed_count = 0
        for outbox_item, event in results:
            success = self._process_item(outbox_item, event)
            if success:
                processed_count += 1

        self._db.commit()
        return processed_count

    def _process_item(self, outbox_item: P5Outbox, event: P5Event) -> bool:
        # Mark IN_PROGRESS
        outbox_item.status = "IN_PROGRESS"
        outbox_item.locked_at = _now()
        outbox_item.attempts += 1
        
        # Build payload
        payload = {
            "event_id": event.event_id,
            "operation_id": event.operation_id,
            "p5_truth_state": event.asserted_truth_state or event.previous_truth_state,
            "event_hash": event.event_hash,
            "previous_event_hash": event.previous_event_hash,
        }
        if event.proof_subject_hash:
            payload["proof_subject_hash"] = event.proof_subject_hash
        if event.cappo_decision_id:
            payload["cappo_decision_id"] = event.cappo_decision_id
            
        payload["payload_hash"] = outbox_item.payload_hash

        try:
            self._transport_fn(payload)
            # Success
            outbox_item.status = "SENT"
            outbox_item.sent_at = _now()
            outbox_item.last_error = None
            return True
            
        except OutboxTransportError as e:
            # Transient failure
            outbox_item.last_error = str(e)
            if outbox_item.attempts >= self._max_attempts:
                outbox_item.status = "DEAD_LETTER"
                outbox_item.dead_lettered_at = _now()
            else:
                outbox_item.status = "FAILED_RETRYABLE"
            return False
            
        except Exception as e:
            # Unexpected failure (poison pill)
            outbox_item.last_error = f"Unexpected failure: {str(e)}"
            if outbox_item.attempts >= self._max_attempts:
                outbox_item.status = "DEAD_LETTER"
                outbox_item.dead_lettered_at = _now()
            else:
                outbox_item.status = "FAILED_RETRYABLE"
            return False
