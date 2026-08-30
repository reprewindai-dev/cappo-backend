"""Canonical append-only lifecycle recorder for authorized consequences.

Authorization and consequence truth are intentionally separate. A
CapabilityActionReceipt proves CAPPO authorized an operation. This module
persists AUTHORIZED and STARTED before provider dispatch, then appends a
terminal or uncertain outcome after the executor settles.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceInvariantViolation,
    ConsequenceState,
    _ALLOWED_TRANSITIONS,
    build_proof_subject_hash,
)
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.executor import (
    Executor,
    ExecutorUnavailableError,
    ProviderExecutionError,
    TerminalExecutionError,
)


class ConsequenceLifecycleRecorder:
    """Persist one event-sourced consequence lifecycle for a CAPPO receipt."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def begin(
        self,
        *,
        receipt_id: str,
        operation_id: str,
        intent_hash: str,
        resource: str | None = None,
    ) -> None:
        """Commit AUTHORIZED→STARTED before the consequence callback executes."""
        receipt = self._db.get(CapabilityActionReceipt, receipt_id)
        if receipt is None or receipt.decision != "allow":
            raise ConsequenceInvariantViolation(
                "consequence start requires a persisted CAPPO ALLOW receipt"
            )

        existing = self._db.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.operation_id == operation_id)
            .order_by(ConsequenceExecutionEvent.version.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if existing.intent_hash != intent_hash:
                raise ConsequenceInvariantViolation(
                    "operation_id is already bound to a different consequence intent"
                )
            raise ConsequenceInvariantViolation(
                f"operation_id already exists in state {existing.state}"
            )

        effective_resource = resource or receipt.resource
        authorized_proof = build_proof_subject_hash(
            operation_id=operation_id,
            intent_hash=intent_hash,
            previous_truth_state="none",
            asserted_truth_state=ConsequenceState.AUTHORIZED.value,
            consequence_identity=receipt.receipt_id,
            canonical_asserted_proposition=(
                f"authorize {receipt.action} on {effective_resource or '*'}"
            ),
        )
        authorized = ConsequenceExecutionEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            operation_id=operation_id,
            intent_hash=intent_hash,
            state=ConsequenceState.AUTHORIZED.value,
            version=0,
            receipt_id=receipt.receipt_id,
            mount_id=receipt.mount_id,
            execution_id=receipt.execution_id,
            principal=receipt.principal,
            action=receipt.action,
            resource=effective_resource,
            proof_subject_hash=authorized_proof,
        )
        started_proof = build_proof_subject_hash(
            operation_id=operation_id,
            intent_hash=intent_hash,
            previous_truth_state=ConsequenceState.AUTHORIZED.value,
            asserted_truth_state=ConsequenceState.STARTED.value,
            consequence_identity=receipt.receipt_id,
            canonical_asserted_proposition=(
                f"execution_started {receipt.action} on {effective_resource or '*'}"
            ),
        )
        started = ConsequenceExecutionEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            operation_id=operation_id,
            intent_hash=intent_hash,
            state=ConsequenceState.STARTED.value,
            version=1,
            receipt_id=receipt.receipt_id,
            mount_id=receipt.mount_id,
            execution_id=receipt.execution_id,
            principal=receipt.principal,
            action=receipt.action,
            resource=effective_resource,
            proof_subject_hash=started_proof,
        )
        self._db.add_all([authorized, started])
        # Deliberate durability boundary: STARTED must survive a process crash
        # immediately before or during the external callback.
        self._db.commit()

    def complete(
        self,
        *,
        operation_id: str,
        succeeded: bool,
        outcome_uncertain: bool = False,
        error_summary: str | None = None,
        proof_type: str = "callback_return",
        proof_ref: str | None = None,
    ) -> ConsequenceExecutionEvent:
        """Append a terminal/uncertain event without mutating prior evidence."""
        latest = self._db.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.operation_id == operation_id)
            .order_by(ConsequenceExecutionEvent.version.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if latest is None:
            raise ConsequenceInvariantViolation(
                "cannot complete a consequence with no persisted STARTED event"
            )
        if latest.state != ConsequenceState.STARTED.value:
            raise ConsequenceInvariantViolation(
                f"cannot complete consequence from state {latest.state}"
            )

        if outcome_uncertain:
            target = ConsequenceState.OUTCOME_UNKNOWN
            effective_proof_type = "outcome_uncertain"
        elif succeeded:
            target = ConsequenceState.SUCCEEDED
            effective_proof_type = proof_type
        else:
            target = ConsequenceState.FAILED
            effective_proof_type = proof_type or "callback_exception"

        if target not in _ALLOWED_TRANSITIONS[ConsequenceState.STARTED]:
            raise ConsequenceInvariantViolation(
                f"illegal consequence transition started -> {target.value}"
            )

        proof_hash = build_proof_subject_hash(
            operation_id=operation_id,
            intent_hash=latest.intent_hash,
            previous_truth_state=latest.state,
            asserted_truth_state=target.value,
            consequence_identity=latest.receipt_id or "unknown",
            canonical_asserted_proposition=(
                f"{target.value} {latest.action} on {latest.resource or '*'} "
                f"with_proof {effective_proof_type}"
            ),
        )
        event = ConsequenceExecutionEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            operation_id=operation_id,
            intent_hash=latest.intent_hash,
            state=target.value,
            version=latest.version + 1,
            receipt_id=latest.receipt_id,
            mount_id=latest.mount_id,
            execution_id=latest.execution_id,
            principal=latest.principal,
            action=latest.action,
            resource=latest.resource,
            completion_proof_type=effective_proof_type,
            completion_proof_ref=proof_ref,
            error_summary=error_summary,
            proof_subject_hash=proof_hash,
        )
        self._db.add(event)
        self._db.commit()
        return event


class ConsequenceLifecycleExecutor:
    """Executor decorator that makes P5 lifecycle evidence unavoidable.

    It cannot create authority: construction requires an already-persisted
    CAPPO ALLOW receipt. The wrapper is installed only after the CapabilityLease
    evaluator returns ALLOW. The delegate remains the actual provider executor.
    """

    def __init__(
        self,
        *,
        db: Session,
        delegate: Executor,
        receipt_id: str,
        operation_id: str,
        intent_hash: str,
        resource: str | None = None,
    ) -> None:
        self._delegate = delegate
        self._recorder = ConsequenceLifecycleRecorder(db)
        self._receipt_id = receipt_id
        self._operation_id = operation_id
        self._intent_hash = intent_hash
        self._resource = resource

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self._recorder.begin(
            receipt_id=self._receipt_id,
            operation_id=self._operation_id,
            intent_hash=self._intent_hash,
            resource=self._resource,
        )
        try:
            result = self._delegate.execute(request)
        except TerminalExecutionError as exc:
            self._recorder.complete(
                operation_id=self._operation_id,
                succeeded=False,
                error_summary=str(exc),
                proof_type="callback_exception",
            )
            raise
        except (ProviderExecutionError, ExecutorUnavailableError) as exc:
            # A transport/provider failure can occur after a remote system has
            # accepted the request. Do not overclaim FAILED without observation.
            self._recorder.complete(
                operation_id=self._operation_id,
                succeeded=False,
                outcome_uncertain=True,
                error_summary=str(exc),
            )
            raise
        except Exception as exc:
            self._recorder.complete(
                operation_id=self._operation_id,
                succeeded=False,
                outcome_uncertain=True,
                error_summary=str(exc),
            )
            raise

        self._recorder.complete(
            operation_id=self._operation_id,
            succeeded=True,
            proof_type="callback_return",
            proof_ref=sha256_json(result),
        )
        return result
