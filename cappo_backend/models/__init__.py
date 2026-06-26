"""SQLAlchemy models for the CAPPO runtime.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogeneration and ``create_all`` see the full schema.
"""

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.execution_authorization import ExecutionAuthorization
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.genome import Genome, GenomeLineage
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.license_key import LicenseKey
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.vnp_models import (
    APIState,
    ComplianceAuditLog,
    PerformanceLeaderboard,
    RegionalTelemetry,
    VNPTransaction,
    VNPUser,
    VNPProvider,
    ProbeEvent,
    RouteSnapshot,
    VNPSDKCredential,
    VNPValidator,
    VNPIncident,
)
from cappo_backend.models.workspace_budget import WorkspaceBudget
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment

__all__ = [
    "AuditEvent",
    "ExecutionAuthorization",
    "ExecutionIdentity",
    "GovernedRun",
    "KillSwitch",
    "LicenseKey",
    "PGLCertificate",
    "PGLLedgerEvent",
    "WorkspaceBudget",
    "X402ConsumedPayment",
    "Genome",
    "GenomeLineage",
    "APIState",
    "ComplianceAuditLog",
    "PerformanceLeaderboard",
    "RegionalTelemetry",
    "VNPTransaction",
    "VNPUser",
    "VNPProvider",
    "ProbeEvent",
    "RouteSnapshot",
    "VNPSDKCredential",
    "VNPValidator",
    "VNPIncident",
]
