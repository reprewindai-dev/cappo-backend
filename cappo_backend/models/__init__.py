"""SQLAlchemy models for the CAPPO runtime.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogeneration and ``create_all`` see the full schema.
"""

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.workspace_budget import WorkspaceBudget

__all__ = [
    "AuditEvent",
    "ExecutionIdentity",
    "GovernedRun",
    "KillSwitch",
    "PGLCertificate",
    "PGLLedgerEvent",
    "WorkspaceBudget",
]
