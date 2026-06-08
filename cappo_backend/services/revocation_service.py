"""RevocationService — DB-backed ExecutionIdentity revocation.

EI Plan §Rollout Phase 4 ("add revocation checks"). Revocation is mutable state
recorded *after* issuance, so it is stored on the ``execution_identities`` row
(``revoked`` / ``revoked_at``) and never folded into the signed identity body.
The gateway consults :meth:`is_revoked` as rule 9 rather than trusting only the
in-object flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.services.audit_service import AuditService

EI_REVOKED = "ei_revoked"


class UnknownExecutionIdentityError(LookupError):
    """Raised when revoking an execution_id that does not exist."""


class RevocationService:
    def __init__(self, db: Session, audit: AuditService) -> None:
        self._db = db
        self._audit = audit

    def is_revoked(self, execution_id: str) -> bool:
        ei = self._db.get(ExecutionIdentity, execution_id)
        return bool(ei and ei.revoked)

    def revoke(
        self,
        execution_id: str,
        *,
        reason: str | None = None,
        workspace_id: str | None = None,
    ) -> ExecutionIdentity:
        ei = self._db.get(ExecutionIdentity, execution_id)
        if ei is None:
            raise UnknownExecutionIdentityError(execution_id)

        if not ei.revoked:
            ei.revoked = True
            ei.revoked_at = datetime.now(timezone.utc)
            self._db.flush()
            self._audit.record(
                EI_REVOKED,
                {"execution_id": execution_id, "reason": reason},
                workspace_id=workspace_id or ei.workspace_id,
                run_id=ei.run_id,
            )
        return ei
