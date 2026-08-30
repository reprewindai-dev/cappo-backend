"""First-party observable consequence for Veklom Activation.

The Activation target is intentionally boring infrastructure: one durable row
per governed execution. That makes it useful as an independent observer. The
row is not an authorization receipt and is not derived from an HTTP success
code; its existence is the consequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cappo_backend.models.activation_consequence import ActivationConsequence
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.executor import TerminalExecutionError

ACTIVATION_PACKAGE_ID = "veklom.activation@v1"
ACTIVATION_WRITE_ACTION = "activation.marker.write"
ACTIVATION_BLOCKED_ACTION = "activation.marker.delete"
ACTIVATION_OBSERVE_ACTION = "activation.marker.observe"
ACTIVATION_PROVIDER = "veklom-activation-target"


class ActivationTargetInvariantError(TerminalExecutionError):
    """Fail-closed error for a malformed or conflicting Activation consequence."""

    error_code = "ACTIVATION_TARGET_INVARIANT_VIOLATION"


@dataclass(frozen=True)
class ActivationObservation:
    execution_id: str
    workspace_id: str
    consequence_count: int
    consequence_id: str | None = None
    operation_id: str | None = None
    mount_id: str | None = None
    receipt_id: str | None = None
    content_hash: str | None = None
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "workspace_id": self.workspace_id,
            "consequence_count": self.consequence_count,
            "consequence_id": self.consequence_id,
            "operation_id": self.operation_id,
            "mount_id": self.mount_id,
            "receipt_id": self.receipt_id,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "observation_source": "activation_consequences",
            "persisted": self.consequence_count > 0,
        }


def _required_string(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ActivationTargetInvariantError(
            f"Activation target requires server-bound {field}."
        )
    return value.strip()


def _canonical_consequence(
    *,
    workspace_id: str,
    execution_id: str,
    operation_id: str,
    mount_id: str,
    receipt_id: str,
    action: str,
    marker_value: str,
) -> dict[str, str]:
    return {
        "workspace_id": workspace_id,
        "execution_id": execution_id,
        "operation_id": operation_id,
        "mount_id": mount_id,
        "receipt_id": receipt_id,
        "action": action,
        "marker_value": marker_value,
    }


def _matches(row: ActivationConsequence, canonical: dict[str, str]) -> bool:
    return (
        row.workspace_id == canonical["workspace_id"]
        and row.execution_id == canonical["execution_id"]
        and row.operation_id == canonical["operation_id"]
        and row.mount_id == canonical["mount_id"]
        and row.receipt_id == canonical["receipt_id"]
        and row.action == canonical["action"]
        and row.marker_value == canonical["marker_value"]
        and row.content_hash == sha256_json(canonical)
    )


class ActivationTargetExecutor:
    """Commit exactly one independently observable Activation consequence.

    This executor is safe to construct only after CAPPO has consumed a real
    CapabilityLease. It also validates the server-injected provenance fields so
    it cannot silently accept a generic/legacy execution request.
    """

    provider = ACTIVATION_PROVIDER
    model = "durable-marker-v1"

    def __init__(self, db: Session) -> None:
        self._db = db

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        action = _required_string(request, "action")
        if action != ACTIVATION_WRITE_ACTION:
            raise ActivationTargetInvariantError(
                f"Activation target refuses unsupported action {action!r}."
            )

        workspace_id = _required_string(request, "workspace_id")
        execution_id = _required_string(request, "capability_execution_id")
        bound_execution_id = _required_string(request, "execution_id")
        if execution_id != bound_execution_id:
            raise ActivationTargetInvariantError(
                "Activation target execution identifiers are not identical."
            )
        mount_id = _required_string(request, "capability_mount_id")
        receipt_id = _required_string(request, "capability_receipt_id")
        operation_id = f"exec:{execution_id}"
        marker_value = f"veklom-activation:{workspace_id}:{execution_id}"
        canonical = _canonical_consequence(
            workspace_id=workspace_id,
            execution_id=execution_id,
            operation_id=operation_id,
            mount_id=mount_id,
            receipt_id=receipt_id,
            action=action,
            marker_value=marker_value,
        )
        content_hash = sha256_json(canonical)

        existing = self._db.execute(
            select(ActivationConsequence)
            .where(
                or_(
                    ActivationConsequence.execution_id == execution_id,
                    ActivationConsequence.operation_id == operation_id,
                )
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            if not _matches(existing, canonical):
                raise ActivationTargetInvariantError(
                    "Activation execution id is already bound to a different target consequence."
                )
            return self._result(existing, idempotent_replay=True)

        row = ActivationConsequence(
            workspace_id=workspace_id,
            execution_id=execution_id,
            operation_id=operation_id,
            mount_id=mount_id,
            receipt_id=receipt_id,
            action=action,
            marker_value=marker_value,
            content_hash=content_hash,
        )
        self._db.add(row)
        try:
            # This is the independent consequence durability boundary. P5
            # STARTED has already been committed by the lifecycle decorator.
            self._db.commit()
        except IntegrityError as exc:
            self._db.rollback()
            raced = self._db.execute(
                select(ActivationConsequence).where(
                    or_(
                        ActivationConsequence.execution_id == execution_id,
                        ActivationConsequence.operation_id == operation_id,
                    )
                )
            ).scalar_one_or_none()
            if raced is not None and _matches(raced, canonical):
                return self._result(raced, idempotent_replay=True)
            raise ActivationTargetInvariantError(
                "Activation target uniqueness fence rejected a conflicting consequence."
            ) from exc

        self._db.refresh(row)
        return self._result(row, idempotent_replay=False)

    def completion_proof(self, result: dict[str, Any]) -> tuple[str, str]:
        """Re-observe the durable row before P5 is allowed to assert SUCCEEDED."""
        target = result.get("activation_target")
        if not isinstance(target, dict):
            raise ActivationTargetInvariantError(
                "Activation result contains no durable target commitment."
            )
        consequence_id = target.get("consequence_id")
        content_hash = target.get("content_hash")
        execution_id = target.get("execution_id")
        if not all(
            isinstance(value, str) and value
            for value in (consequence_id, content_hash, execution_id)
        ):
            raise ActivationTargetInvariantError(
                "Activation result contains an incomplete target commitment."
            )
        row = self._db.get(ActivationConsequence, consequence_id)
        if (
            row is None
            or row.execution_id != execution_id
            or row.content_hash != content_hash
        ):
            raise ActivationTargetInvariantError(
                "Activation target could not re-observe its committed consequence."
            )
        return "durable_target_row", row.content_hash

    def _result(
        self,
        row: ActivationConsequence,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        return {
            "response": "Veklom Activation durable marker committed.",
            "provider": self.provider,
            "model": self.model,
            "tokens": 0,
            "activation_target": {
                "consequence_id": row.consequence_id,
                "execution_id": row.execution_id,
                "operation_id": row.operation_id,
                "workspace_id": row.workspace_id,
                "mount_id": row.mount_id,
                "receipt_id": row.receipt_id,
                "content_hash": row.content_hash,
                "created_at": row.created_at.isoformat(),
                "idempotent_replay": idempotent_replay,
            },
        }


def observe_activation_consequence(
    db: Session,
    *,
    execution_id: str,
    workspace_id: str,
) -> ActivationObservation:
    """Observe the target table directly; never infer from provider/P5 state."""
    count = db.execute(
        select(func.count(ActivationConsequence.consequence_id)).where(
            ActivationConsequence.execution_id == execution_id,
            ActivationConsequence.workspace_id == workspace_id,
        )
    ).scalar_one()
    row = db.execute(
        select(ActivationConsequence).where(
            ActivationConsequence.execution_id == execution_id,
            ActivationConsequence.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return ActivationObservation(
            execution_id=execution_id,
            workspace_id=workspace_id,
            consequence_count=int(count),
        )
    return ActivationObservation(
        execution_id=execution_id,
        workspace_id=workspace_id,
        consequence_count=int(count),
        consequence_id=row.consequence_id,
        operation_id=row.operation_id,
        mount_id=row.mount_id,
        receipt_id=row.receipt_id,
        content_hash=row.content_hash,
        created_at=row.created_at.isoformat(),
    )
