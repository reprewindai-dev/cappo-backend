"""P5 Execution-Truth Invariant — State enumerations.

These enums are the canonical vocabulary for the P5 truth-state machine.
No ledger, proof layer, or control-plane projection may record a consequence
as COMPLETED_SUCCESS unless a valid completion proof exists and is
cryptographically bound to the operation.
"""

from __future__ import annotations

from enum import Enum


class TruthState(str, Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    COMPLETED_SUCCESS = "COMPLETED_SUCCESS"
    COMPLETED_FAILURE = "COMPLETED_FAILURE"
    OBSERVED_EFFECT = "OBSERVED_EFFECT"
    COMPENSATED = "COMPENSATED"
    ABANDONED_REQUIRES_HUMAN = "ABANDONED_REQUIRES_HUMAN"


class SinkClass(str, Enum):
    """Sink classes define the idempotency and compensatability contract."""
    A_TRANSACTIONAL_LOCAL = "A_TRANSACTIONAL_LOCAL"
    B_IDEMPOTENT_EXTERNAL = "B_IDEMPOTENT_EXTERNAL"
    C_QUERYABLE_EXTERNAL = "C_QUERYABLE_EXTERNAL"
    D_COMPENSATABLE = "D_COMPENSATABLE"
    E_NON_IDEMPOTENT = "E_NON_IDEMPOTENT"


class P5EventType(str, Enum):
    INTENT_REQUESTED = "p5.intent.requested"
    CAPPO_AUTHORIZED = "p5.cappo.authorized"
    EXECUTION_STARTED = "p5.execution.started"
    OUTCOME_UNKNOWN = "p5.outcome.unknown"
    TRUTH_TRANSITION_REQUESTED = "p5.truth.transition.requested"
    TRUTH_COMPLETED_SUCCESS = "p5.truth.completed_success"
    TRUTH_COMPLETED_FAILURE = "p5.truth.completed_failure"
    OBSERVED_EFFECT = "p5.observed_effect"
    COMPENSATED = "p5.compensated"
    ABANDONED_REQUIRES_HUMAN = "p5.abandoned.requires_human"


class CAPPOTruthDecision(str, Enum):
    TRUTH_ALLOW = "TRUTH_ALLOW"
    TRUTH_DENY = "TRUTH_DENY"
    TRUTH_CONSTRAIN = "TRUTH_CONSTRAIN"
    TRUTH_ESCALATE = "TRUTH_ESCALATE"
    TRUTH_DEFER = "TRUTH_DEFER"

class AssuranceLevel(str, Enum):
    """
    Evidence Assurance Taxonomy.
    Cryptography provides tamper-evidence, but assurance classifications define
    the physical provenance of the finality evidence.
    """
    E0_ORCHESTRATOR_ASSERTION = "E0_ORCHESTRATOR_ASSERTION"
    E1_CONNECTOR_ATTESTED = "E1_CONNECTOR_ATTESTED"
    E2_TARGET_CONFIRMED = "E2_TARGET_CONFIRMED"
    E3_INDEPENDENTLY_CORROBORATED = "E3_INDEPENDENTLY_CORROBORATED"
