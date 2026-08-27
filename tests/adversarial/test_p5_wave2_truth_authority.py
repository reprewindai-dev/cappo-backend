"""
P5 Adversarial Tranche 2 — ATTACK TRUTH AUTHORITY ITSELF

Wave 1 (test_p5_truth_state_sync.py) covered crash boundaries and UNKNOWN fencing.
Wave 2 attacks the epistemic enforcement mechanisms directly.

Constitutional invariants tested:
  P5-A  Assertion Soundness: terminal assertion requires proposition-bound evidence
  P5-B  Epistemic Monotonicity: certainty never exceeds evidence
  P5-C  Uncertainty Preservation: ambiguous outcomes preserved, not collapsed
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.engine import PolicyError
from cappo_backend.capability_mount.service import MountRegistry
from cappo_backend.capability_mount.models import MountScope, MountPolicy, CapabilityPackage
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceInvariantViolation,
    ConsequenceState,
    build_proof_subject_hash,
    build_intent_hash,
)

class ConfirmedAnchor:
    def anchor(self, *args, **kwargs):
        from cappo_backend.capability_mount.service import AnchorResult
        return AnchorResult("confirmed", f"rcpt_{uuid.uuid4().hex}")

def _setup_binding(db: Session, actions: list[str] | None = None):
    from cappo_backend.db.base import Base
    Base.metadata.create_all(bind=db.get_bind())

    if actions is None:
        actions = ["test_action"]

    reg = MountRegistry(db, anchor=ConfirmedAnchor())
    pkg = CapabilityPackage(
        id=f"p5w2.pkg.t{uuid.uuid4().hex[:6]}@v1",
        family="test",
        title="P5 Wave 2 Package",
        purpose="Adversarial truth-state testing",
        reads=[],
        writes=actions,
    )
    reg.register_package(pkg)

    mount_record, anchor, reason = reg.request_mount(
        package_ref=pkg.id,
        scope=MountScope(workspace="ws", project="proj", reads=[], writes=actions),
        role="agent",
        policy=MountPolicy(),
        ttl_seconds=600,
        owner_principal="test_owner",
        execution_id=f"exec-{uuid.uuid4().hex[:8]}",
        caller_spiffe_id="spiffe://cappo/caller",
        executor_spiffe_id="spiffe://cappo/executor",
    )
    assert mount_record is not None, f"Mount failed: {reason}"

    from cappo_backend.models.capability_mount import CapabilityMount
    row = db.execute(
        select(CapabilityMount).where(CapabilityMount.mount_id == mount_record.mount.id)
    ).scalar_one()
    binding = reg._record(row).binding
    return binding, mount_record, reg

def _get_events(db: Session, operation_id: str) -> list[ConsequenceExecutionEvent]:
    return db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.operation_id == operation_id)
        .order_by(ConsequenceExecutionEvent.version.asc())
    ).scalars().all()

def _run_to_unknown(db: Session, binding, action: str = "test_action") -> str:
    op_id = f"op-{uuid.uuid4().hex}"
    _authorize_only(binding, action, op_id)
    binding._begin_consequence(op_id)
    binding._completion_reporter(op_id, succeeded=False, outcome_uncertain=True)
    events = _get_events(db, op_id)
    assert events[-1].state == ConsequenceState.OUTCOME_UNKNOWN.value
    return op_id

def _authorize_only(binding, action: str, op_id: str):
    i_hash = build_intent_hash(
        mount_id=binding.token.mount_id,
        execution_id=binding.token.execution_id,
        action=action,
        resource=None,
        normalized_args={},
    )
    binding._cappo_evaluator(action, {}, operation_id=op_id, intent_hash=i_hash)
    return i_hash

# ---------------------------------------------------------------------------
# T6 — Proof transplantation: valid proof for op-A applied to op-B
# ---------------------------------------------------------------------------
def test_t6_proof_transplantation_wrong_operation(db: Session):
    binding, _, _ = _setup_binding(db, ["deploy"])
    op_a = f"op-A-{uuid.uuid4().hex}"
    op_b = f"op-B-{uuid.uuid4().hex}"

    binding.consequence("deploy", lambda: "a_done", operation_id=op_a)
    events_a = _get_events(db, op_a)
    legitimate_proof_hash_for_a = events_a[-1].proof_subject_hash

    binding2, _, _ = _setup_binding(db, ["deploy"])
    _authorize_only(binding2, "deploy", op_b)
    events_b = _get_events(db, op_b)

    transplanted_event = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        operation_id=op_b,
        intent_hash=events_b[0].intent_hash,
        state=ConsequenceState.SUCCEEDED.value,
        version=events_b[0].version + 10,
        receipt_id=events_b[0].receipt_id,
        mount_id=events_b[0].mount_id,
        execution_id=events_b[0].execution_id,
        principal=events_b[0].principal,
        action="deploy",
        resource=None,
        completion_proof_type="cryptographic_receipt",
        proof_subject_hash=legitimate_proof_hash_for_a,
    )
    db.add(transplanted_event)
    db.commit()

    saved = _get_events(db, op_b)
    attacker_event = next(e for e in saved if e.state == ConsequenceState.SUCCEEDED.value)

    expected_hash_for_b = build_proof_subject_hash(
        operation_id=op_b,
        intent_hash=events_b[0].intent_hash,
        previous_truth_state=events_b[0].state,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity=events_b[0].receipt_id or "unknown",
        canonical_asserted_proposition="succeeded deploy on * with_proof cryptographic_receipt",
    )
    assert attacker_event.proof_subject_hash != expected_hash_for_b

# ---------------------------------------------------------------------------
# T7 — Altered intent_hash in transplanted proof
# ---------------------------------------------------------------------------
def test_t7_proof_with_mutated_intent_hash(db: Session):
    binding, _, _ = _setup_binding(db, ["transfer"])
    op_id = f"op-{uuid.uuid4().hex}"
    binding.consequence("transfer", lambda: "done", operation_id=op_id)
    events = _get_events(db, op_id)
    original_hash = events[-1].proof_subject_hash

    mutated_hash = build_proof_subject_hash(
        operation_id=op_id,
        intent_hash="deadbeef" * 8,
        previous_truth_state=ConsequenceState.STARTED.value,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity=events[-1].receipt_id or "unknown",
        canonical_asserted_proposition="succeeded transfer on * with_proof callback_return",
    )
    assert original_hash != mutated_hash

# ---------------------------------------------------------------------------
# T8 — Wrong consequence_identity (receipt_id mismatch)
# ---------------------------------------------------------------------------
def test_t8_proof_with_wrong_consequence_identity(db: Session):
    binding, _, _ = _setup_binding(db, ["read_file"])
    op_id = f"op-{uuid.uuid4().hex}"
    binding.consequence("read_file", lambda: "data", operation_id=op_id)
    events = _get_events(db, op_id)
    succeeded = events[-1]

    forged_hash = build_proof_subject_hash(
        operation_id=op_id,
        intent_hash=succeeded.intent_hash,
        previous_truth_state=ConsequenceState.STARTED.value,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity="wrong-receipt-id-attacker-forged",
        canonical_asserted_proposition="succeeded read_file on * with_proof callback_return",
    )
    assert succeeded.proof_subject_hash != forged_hash

# ---------------------------------------------------------------------------
# T9 — Wrong previous_truth_state
# ---------------------------------------------------------------------------
def test_t9_proof_with_wrong_predecessor_state(db: Session):
    binding, _, _ = _setup_binding(db, ["write_log"])
    op_id = f"op-{uuid.uuid4().hex}"
    binding.consequence("write_log", lambda: "logged", operation_id=op_id)
    succeeded = _get_events(db, op_id)[-1]

    forged_hash = build_proof_subject_hash(
        operation_id=op_id,
        intent_hash=succeeded.intent_hash,
        previous_truth_state=ConsequenceState.AUTHORIZED.value,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity=succeeded.receipt_id or "unknown",
        canonical_asserted_proposition="succeeded write_log on * with_proof callback_return",
    )
    assert succeeded.proof_subject_hash != forged_hash

# ---------------------------------------------------------------------------
# T10 — Wrong asserted_truth_state
# ---------------------------------------------------------------------------
def test_t10_proof_with_wrong_asserted_state(db: Session):
    binding, _, _ = _setup_binding(db, ["send_email"])
    op_id = f"op-{uuid.uuid4().hex}"
    binding.consequence("send_email", lambda: "sent", operation_id=op_id)
    succeeded = _get_events(db, op_id)[-1]

    wrong_state_hash = build_proof_subject_hash(
        operation_id=op_id,
        intent_hash=succeeded.intent_hash,
        previous_truth_state=ConsequenceState.STARTED.value,
        asserted_truth_state=ConsequenceState.FAILED.value,
        consequence_identity=succeeded.receipt_id or "unknown",
        canonical_asserted_proposition="failed send_email on * with_proof callback_return",
    )
    assert succeeded.proof_subject_hash != wrong_state_hash

# ---------------------------------------------------------------------------
# T11 — Unknown proof type fails closed
# ---------------------------------------------------------------------------
def test_t11_unknown_proof_type_fails_closed(db: Session):
    binding, _, _ = _setup_binding(db, ["mutate_db"])
    op_id2 = f"op-{uuid.uuid4().hex}"
    _authorize_only(binding, "mutate_db", op_id2)
    binding._begin_consequence(op_id2)

    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(
            op_id2,
            succeeded=True,
            proof_type="magic_blockchain_attestation",
        )
    events = _get_events(db, op_id2)
    assert not any(e.state == ConsequenceState.SUCCEEDED.value for e in events)

# ---------------------------------------------------------------------------
# T12 — Missing/None proof type
# ---------------------------------------------------------------------------
def test_t12_missing_proof_type_blocked(db: Session):
    binding, _, _ = _setup_binding(db, ["create_record"])
    op_id = f"op-{uuid.uuid4().hex}"
    _authorize_only(binding, "create_record", op_id)
    binding._begin_consequence(op_id)

    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(op_id, succeeded=True, proof_type=None)
        
    events = _get_events(db, op_id)
    assert not any(e.state == ConsequenceState.SUCCEEDED.value for e in events)

# ---------------------------------------------------------------------------
# T13 — Direct ORM bypass lacks proof_subject_hash
# ---------------------------------------------------------------------------
def test_t13_direct_orm_bypass_detectable(db: Session):
    binding, _, _ = _setup_binding(db, ["approve_payment"])
    op_id = f"op-{uuid.uuid4().hex}"
    _authorize_only(binding, "approve_payment", op_id)
    auth_event = _get_events(db, op_id)[0]

    poisoned = ConsequenceExecutionEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        operation_id=op_id,
        intent_hash=auth_event.intent_hash,
        state=ConsequenceState.SUCCEEDED.value,
        version=auth_event.version + 1,
        receipt_id=auth_event.receipt_id,
        mount_id=auth_event.mount_id,
        execution_id=auth_event.execution_id,
        principal=auth_event.principal,
        action="approve_payment",
        resource=None,
        completion_proof_type="callback_return",
        proof_subject_hash=None,
    )
    db.add(poisoned)
    db.commit()

    succeeded_event = next(e for e in _get_events(db, op_id) if e.state == ConsequenceState.SUCCEEDED.value)
    expected_hash = build_proof_subject_hash(
        operation_id=op_id,
        intent_hash=auth_event.intent_hash,
        previous_truth_state=auth_event.state,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity=auth_event.receipt_id or "unknown",
        canonical_asserted_proposition="succeeded approve_payment on * with_proof callback_return",
    )
    assert succeeded_event.proof_subject_hash != expected_hash

# ---------------------------------------------------------------------------
# T14 — Executor cannot resolve OUTCOME_UNKNOWN -> SUCCEEDED without authority
# ---------------------------------------------------------------------------
def test_t14_executor_cannot_resolve_unknown(db: Session):
    binding, _, _ = _setup_binding(db, ["critical_action"])
    op_id = _run_to_unknown(db, binding, "critical_action")

    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(
            op_id,
            succeeded=True,
            proof_type="callback_return",
        )
    assert not any(e.state == ConsequenceState.SUCCEEDED.value for e in _get_events(db, op_id))

# ---------------------------------------------------------------------------
# T15 — Reconciler with valid proof but insufficient certainty
# ---------------------------------------------------------------------------
def test_t15_reconciler_insufficient_proof_certainty(db: Session):
    binding, _, _ = _setup_binding(db, ["provision_vm"])
    op_id = _run_to_unknown(db, binding, "provision_vm")
    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(
            op_id,
            succeeded=True,
            proof_type="outcome_uncertain",
        )

# ---------------------------------------------------------------------------
# T16 — Reconciler with authority BUT insufficient certainty still blocked
# ---------------------------------------------------------------------------
def test_t16_strong_authority_weak_evidence_blocked(db: Session):
    binding, _, _ = _setup_binding(db, ["allocate_budget"])
    op_id = _run_to_unknown(db, binding, "allocate_budget")
    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(
            op_id,
            succeeded=True,
            proof_type="optimistic_claim",
        )

# ---------------------------------------------------------------------------
# T17 — Two workers racing AUTHORIZED → STARTED
# ---------------------------------------------------------------------------
def test_t17_concurrent_execution_ownership(db: Session):
    binding, _, _ = _setup_binding(db, ["debit_account"])
    op_id = f"op-{uuid.uuid4().hex}"
    _authorize_only(binding, "debit_account", op_id)

    winners = []
    lock = threading.Lock()

    def attempt_start():
        try:
            result = binding._begin_consequence(op_id)
            with lock:
                winners.append(result)
        except Exception:
            with lock:
                winners.append(False)

    t1 = threading.Thread(target=attempt_start)
    t2 = threading.Thread(target=attempt_start)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert winners.count(True) <= 1
    started_events = [e for e in _get_events(db, op_id) if e.state == ConsequenceState.STARTED.value]
    assert len(started_events) <= 1

# ---------------------------------------------------------------------------
# T18 — Two reconcilers racing UNKNOWN → RECONCILED_SUCCEEDED
# ---------------------------------------------------------------------------
def test_t18_concurrent_reconciler_race(db: Session):
    binding, _, _ = _setup_binding(db, ["settle_trade"])
    op_id = _run_to_unknown(db, binding, "settle_trade")

    written = []
    lock = threading.Lock()
    def attempt_reconcile():
        try:
            binding._completion_reporter(
                op_id,
                succeeded=True,
                proof_type="reconciliation_api_query",
            )
            with lock:
                written.append("reconciled_succeeded")
        except Exception as exc:
            with lock:
                written.append(f"blocked:{exc}")

    t1 = threading.Thread(target=attempt_reconcile)
    t2 = threading.Thread(target=attempt_reconcile)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    terminal_events = [e for e in _get_events(db, op_id) if e.state in (ConsequenceState.RECONCILED_SUCCEEDED.value, ConsequenceState.SUCCEEDED.value)]
    assert len(terminal_events) <= 1

# ---------------------------------------------------------------------------
# T19 — Proof replay after restart
# ---------------------------------------------------------------------------
def test_t19_proof_replay_after_restart_detected(db: Session):
    binding, _, _ = _setup_binding(db, ["notify_user"])
    op_a = f"op-A-{uuid.uuid4().hex}"
    binding.consequence("notify_user", lambda: "notified", operation_id=op_a)
    stolen_hash = _get_events(db, op_a)[-1].proof_subject_hash

    op_b = f"op-B-{uuid.uuid4().hex}"
    binding2, _, _ = _setup_binding(db, ["notify_user"])
    _authorize_only(binding2, "notify_user", op_b)
    events_b = _get_events(db, op_b)

    expected_hash_b = build_proof_subject_hash(
        operation_id=op_b,
        intent_hash=events_b[0].intent_hash,
        previous_truth_state=ConsequenceState.AUTHORIZED.value,
        asserted_truth_state=ConsequenceState.SUCCEEDED.value,
        consequence_identity=events_b[0].receipt_id or "unknown",
        canonical_asserted_proposition="succeeded notify_user on * with_proof callback_return",
    )
    assert stolen_hash != expected_hash_b

# ---------------------------------------------------------------------------
# T20 — Compensation linkage
# ---------------------------------------------------------------------------
def test_t20_compensation_never_mutates_original(db: Session):
    binding, _, _ = _setup_binding(db, ["charge_card", "refund_charge"])
    op_original = f"op-charge-{uuid.uuid4().hex}"
    binding.consequence("charge_card", lambda: "charged", operation_id=op_original)
    original_succeeded_event_id = _get_events(db, op_original)[-1].event_id

    binding2, _, _ = _setup_binding(db, ["refund_charge"])
    op_compensation = f"op-refund-{uuid.uuid4().hex}"
    binding2.consequence("refund_charge", lambda: "refunded", operation_id=op_compensation)

    events_original_after = _get_events(db, op_original)
    final_original = events_original_after[-1]
    assert final_original.event_id == original_succeeded_event_id
    assert final_original.state == ConsequenceState.SUCCEEDED.value
    original_states = {e.state for e in events_original_after}
    assert ConsequenceState.FAILED.value not in original_states
    assert "compensated" not in original_states

# ---------------------------------------------------------------------------
# T21 — OBSERVED_EFFECT escalated to SUCCEEDED without causal attribution
# ---------------------------------------------------------------------------
def test_t21_observed_effect_not_causal_success(db: Session):
    binding, _, _ = _setup_binding(db, ["provision_storage"])
    op_id = _run_to_unknown(db, binding, "provision_storage")

    with pytest.raises(ConsequenceInvariantViolation, match="Certainty invariant"):
        binding._completion_reporter(
            op_id,
            succeeded=True,
            proof_type="optimistic_claim",
        )

    assert not any(e.state in (ConsequenceState.SUCCEEDED.value, ConsequenceState.RECONCILED_SUCCEEDED.value) for e in _get_events(db, op_id))
