"""SQLAlchemy models for the CAPPO runtime.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogeneration and ``create_all`` see the full schema.
"""

from cappo_backend.models.audit_event import AuditEvent
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_evidence_consumption import CapabilityEvidenceConsumption
from cappo_backend.models.capability_lease import CapabilityLease
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceInvariantViolation,
    ConsequenceState,
    build_intent_hash,
)
from cappo_backend.models.execution_authorization import ExecutionAuthorization
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.free_run_quota import FreeRunQuota
from cappo_backend.models.genome import Genome, GenomeLineage
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.kill_switch import KillSwitch
from cappo_backend.models.license_key import LicenseKey
from cappo_backend.models.merkle_leaf_sequence import MerkleLeafSequence
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.models.retrieval import (
    DocumentIndex,
    RetrievalSession,
    RetrievalTrace,
    SourceDocument,
)
from cappo_backend.models.runtime_path_assignment import RuntimePathAssignment
from cappo_backend.models.vnp_interlink_nonce import VNPInterlinkNonce
from cappo_backend.models.vnp_models import (
    APIState,
    ComplianceAuditLog,
    PerformanceLeaderboard,
    ProbeEvent,
    RegionalTelemetry,
    RouteSnapshot,
    VNPIncident,
    VNPProvider,
    VNPSDKCredential,
    VNPTransaction,
    VNPUser,
    VNPValidator,
)
from cappo_backend.models.workspace_budget import WorkspaceBudget
from cappo_backend.models.x402_consumed_payment import X402ConsumedPayment

# P5 Execution-Truth Invariant models — must be imported so Base.metadata sees them
from cappo_backend.p5.models import P5Event, P5Operation, P5Outbox  # noqa: F401


__all__ = [
    "FreeRunQuota",
    "AuditEvent",
    "CapabilityActionReceipt",
    "ConsequenceExecutionEvent",
    "ConsequenceState",
    "ConsequenceInvariantViolation",
    "build_intent_hash",
    "CapabilityEvidenceConsumption",
    "CapabilityLease",
    "CapabilityMount",
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
    "VNPInterlinkNonce",
    "SourceDocument",
    "DocumentIndex",
    "RetrievalSession",
    "RetrievalTrace",
    "RuntimePathAssignment",
]

from cappo_backend.models.tenant_provider_credential import TenantProviderCredential
