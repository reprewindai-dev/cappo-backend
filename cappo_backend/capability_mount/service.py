"""Durable capability mount lifecycle service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.engine import mark_mount_revoked
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_evidence_consumption import CapabilityEvidenceConsumption
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.models.consequence_execution import (
    ConsequenceExecutionEvent,
    ConsequenceInvariantViolation,
    ConsequenceState,
    build_proof_subject_hash,
)
from cappo_backend.services.audit_service import AuditService
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.mount_evidence import (
    BoundMountEvidenceVerifier,
    VerifiedMountEvidence,
)

from .effects import EffectTargetRegistry, validate_resource
from .engine import AuditSink, ExecutionBinding, Mounter
from .errors import ExecutionTerminatedError, MountError, PolicyError, TokenExpiredError
from .models import (
    CapabilityPackage,
    Decision,
    EphemeralScopedToken,
    ExecutionAuditEvent,
    Mount,
    MountPolicy,
    MountScope,
    UnmountReason,
)


class DatabaseAuditSink(AuditSink):
    """A real consequence sink that durably writes ExecutionBinding events to the audit ledger."""
    def __init__(self, db: Session, workspace_id: str | None = None, run_id: str | None = None) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.run_id = run_id

    def append(self, event: ExecutionAuditEvent) -> None:
        AuditService(self.db).record(
            operation_type="execution_binding_decision",
            payload=event.model_dump(mode="json"),
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            forward_to_gnomledger=False,  # Can be adjusted based on sync rules
        )


@dataclass(frozen=True)
class AnchorResult:
    status: str
    anchor_id: str | None = None
    detail: str | None = None


class EventAnchor(Protocol):
    def anchor(
        self,
        event_type: str,
        *,
        action: str,
        decision: str,
        reason: str,
        mount: Mount | None,
        token: EphemeralScopedToken | None,
        **kwargs: Any,
    ) -> AnchorResult: ...


class UnconfirmedAnchor:
    """Explicit development anchor used when no external PGL is configured."""

    def anchor(self, event_type: str, **_: Any) -> AnchorResult:
        return AnchorResult("unconfirmed", detail="PGL anchor is not configured")


@dataclass
class MountRecord:
    mount: Mount
    token: EphemeralScopedToken
    binding: ExecutionBinding
    anchoring: AnchorResult | None = None


class MountRegistry:
    """Own package discovery and DB-backed ephemeral mount records."""

    def __init__(
        self,
        db: Session | None = None,
        anchor: EventAnchor | None = None,
        evidence_verifier: BoundMountEvidenceVerifier | None = None,
        effect_targets: EffectTargetRegistry | None = None,
    ) -> None:
        self.db = db
        self.packages: dict[str, CapabilityPackage] = {}
        self.anchor = anchor or UnconfirmedAnchor()
        self.evidence_verifier = evidence_verifier or BoundMountEvidenceVerifier()
        self.effect_targets = effect_targets or EffectTargetRegistry()
        self.mounter = Mounter()

    def register_package(self, package: CapabilityPackage) -> None:
        self.packages[package.id] = package

    def list_packages(self) -> list[CapabilityPackage]:
        return sorted(self.packages.values(), key=lambda package: package.id)

    def _db(self) -> Session:
        if self.db is None:
            raise RuntimeError("durable mount storage requires a database session")
        return self.db

    def _record(self, row: CapabilityMount) -> MountRecord:
        mount = Mount.model_validate(row.mount_json)
        token = EphemeralScopedToken.model_validate(row.token_json).model_copy(
            update={"nonce_consumed": row.nonce_consumed}
        )

        def cappo_evaluator(
            action: str,
            kwargs: dict[str, object],
            *,
            operation_id: str,
            intent_hash: str,
        ) -> tuple[Decision, str, str | None]:
            """Route through CAPPO.evaluate() for consequence dominance.

            Returns (decision, reason, receipt_id | None).
            receipt_id is None when the decision is DENY (no receipt written).
            On ALLOW, also creates ConsequenceExecution in AUTHORIZED state
            bound to the receipt — idempotent on operation_id + intent_hash.
            """
            # Idempotency check: reject same operation_id with different intent
            import uuid
            db = self._db()
            latest = db.execute(
                select(ConsequenceExecutionEvent)
                .where(ConsequenceExecutionEvent.operation_id == operation_id)
                .order_by(ConsequenceExecutionEvent.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            
            if latest is not None:
                if latest.intent_hash != intent_hash:
                    return Decision.DENY, "idempotency_intent_mismatch", None
                # Same operation already in progress or complete — return cached state
                return Decision.DENY, f"idempotency_replay:{latest.state}", latest.receipt_id

            dec, reason, _, detail = self.evaluate(
                mount_id=mount.id,
                action=action,
                resource=str(kwargs.get("resource")) if "resource" in kwargs else None,
                token_id=token.token_id,
                nonce=token.nonce,
                owner_principal=row.owner_principal,
                owner_workspace=row.owner_workspace,
                approval_token=str(kwargs.get("approval_token")) if "approval_token" in kwargs else None,
                suppression_evidence=str(kwargs.get("suppression_evidence")) if "suppression_evidence" in kwargs else None,
                suppression_confirmed=bool(kwargs.get("suppression_confirmed")),
            )
            receipt_id = (detail or {}).get("receipt_id")

            if dec is Decision.ALLOW and receipt_id:
                proof_hash = build_proof_subject_hash(
                    operation_id=operation_id,
                    intent_hash=intent_hash,
                    previous_truth_state="none",
                    asserted_truth_state=ConsequenceState.AUTHORIZED.value,
                    consequence_identity=receipt_id,
                    canonical_asserted_proposition=f"authorize {action} on {kwargs.get('resource', '*')}",
                )
                # Append AUTHORIZED event
                # _SITE: cappo_evaluator
                ce = ConsequenceExecutionEvent(
                    event_id=f"evt_{uuid.uuid4().hex}",
                    operation_id=operation_id,
                    intent_hash=intent_hash,
                    state=ConsequenceState.AUTHORIZED.value,
                    version=0,
                    receipt_id=receipt_id,
                    mount_id=mount.id,
                    execution_id=token.execution_id,
                    principal=row.owner_principal,
                    action=action,
                    resource=str(kwargs.get("resource")) if "resource" in kwargs else None,
                    proof_subject_hash=proof_hash,
                )
                db.add(ce)
                db.commit()

            return dec, reason, receipt_id

        def begin_consequence(operation_id: str) -> bool:
            """Atomically append ConsequenceExecutionEvent(STARTED).
            
            Uses database locking to prevent concurrent workers from both moving
            AUTHORIZED -> STARTED. Returns True if claimed, False if another worker won.
            """
            import uuid
            db = self._db()
            
            # Use FOR UPDATE to serialize access to the latest event for this operation.
            # (In PostgreSQL this blocks concurrent claims; sqlite will lock the DB)
            latest = db.execute(
                select(ConsequenceExecutionEvent)
                .where(ConsequenceExecutionEvent.operation_id == operation_id)
                .order_by(ConsequenceExecutionEvent.version.desc())
                .limit(1)
                .with_for_update()
            ).scalar_one_or_none()

            if latest is None:
                db.commit()
                return False  # No AUTHORIZED event exists
                
            if latest.state != ConsequenceState.AUTHORIZED.value:
                db.commit()
                return False  # Already advanced past AUTHORIZED

            try:
                proof_hash = build_proof_subject_hash(
                    operation_id=operation_id,
                    intent_hash=latest.intent_hash,
                    previous_truth_state=latest.state,
                    asserted_truth_state=ConsequenceState.STARTED.value,
                    consequence_identity=latest.receipt_id or "unknown",
                    canonical_asserted_proposition=f"execution_started {latest.action} on {latest.resource or '*'}",
                )
                # _SITE: begin_consequence
                ce = ConsequenceExecutionEvent(
                    event_id=f"evt_{uuid.uuid4().hex}",
                    operation_id=operation_id,
                    intent_hash=latest.intent_hash,
                    state=ConsequenceState.STARTED.value,
                    version=latest.version + 1,
                    receipt_id=latest.receipt_id,
                    mount_id=latest.mount_id,
                    execution_id=latest.execution_id,
                    principal=latest.principal,
                    action=latest.action,
                    resource=latest.resource,
                    completion_proof_type="optimistic_claim",
                    proof_subject_hash=proof_hash,
                )
                db.add(ce)
                db.commit()
                return True
            except Exception:
                db.rollback()
                return False

        def completion_reporter(
            operation_id: str,
            *,
            succeeded: bool,
            error_summary: str | None = None,
            outcome_uncertain: bool = False,
            proof_type: str = "callback_return",
        ) -> None:
            """Append SUCCEEDED / FAILED / OUTCOME_UNKNOWN event."""
            import uuid
            db = self._db()
            latest = db.execute(
                select(ConsequenceExecutionEvent)
                .where(ConsequenceExecutionEvent.operation_id == operation_id)
                .order_by(ConsequenceExecutionEvent.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            
            if latest is None:
                return  # No record to update

            from cappo_backend.models.consequence_execution import _ALLOWED_TRANSITIONS
            
            if latest.state == ConsequenceState.OUTCOME_UNKNOWN.value:
                if outcome_uncertain:
                    target = ConsequenceState.OUTCOME_UNKNOWN
                    pt = "outcome_uncertain"
                elif succeeded:
                    target = ConsequenceState.RECONCILED_SUCCEEDED
                    pt = proof_type
                else:
                    target = ConsequenceState.RECONCILED_FAILED
                    pt = proof_type
            else:
                if outcome_uncertain:
                    target = ConsequenceState.OUTCOME_UNKNOWN
                    pt = "outcome_uncertain"
                elif succeeded:
                    target = ConsequenceState.SUCCEEDED
                    pt = proof_type
                else:
                    target = ConsequenceState.FAILED
                    pt = proof_type

            # Enforce FSM
            latest_enum = ConsequenceState(latest.state)
            if target != latest_enum and target not in _ALLOWED_TRANSITIONS.get(latest_enum, set()):
                raise ConsequenceInvariantViolation(
                    f"Illegal transition: {latest.state} -> {target.value}"
                )
            terminal = {
                ConsequenceState.SUCCEEDED.value,
                ConsequenceState.FAILED.value,
                ConsequenceState.RECONCILED_SUCCEEDED.value,
                ConsequenceState.RECONCILED_FAILED.value,
            }
            if latest.state in terminal:
                print(f"completion_reporter: operation {operation_id} already in terminal state {latest.state}")
                return

            # Enforce Asserted Certainty <= Evidentiary Certainty
            certainty_levels = {
                "outcome_uncertain": 0,
                "optimistic_claim": 1,
                "callback_return": 1,
                "callback_exception": 1,
                "reconciliation_filesystem": 2,
                "reconciliation_db_query": 2,
                "reconciliation_api_query": 2,
                "cryptographic_receipt": 3,
            }
            state_certainty_requirements = {
                ConsequenceState.OUTCOME_UNKNOWN.value: 0,
                ConsequenceState.SUCCEEDED.value: 1,
                ConsequenceState.FAILED.value: 1,
                ConsequenceState.RECONCILED_SUCCEEDED.value: 2,
                ConsequenceState.RECONCILED_FAILED.value: 2,
            }
            
            ev_certainty = certainty_levels.get(pt, 0)
            req_certainty = state_certainty_requirements.get(target.value, 0)
            if req_certainty > ev_certainty:
                raise ConsequenceInvariantViolation(
                    f"Certainty invariant failed: Asserted state {target.value} requires certainty {req_certainty}, "
                    f"but proof {pt} provides certainty {ev_certainty}."
                )

            try:
                proof_hash = build_proof_subject_hash(
                    operation_id=operation_id,
                    intent_hash=latest.intent_hash,
                    previous_truth_state=latest.state,
                    asserted_truth_state=target.value,
                    consequence_identity=latest.receipt_id or "unknown",
                    canonical_asserted_proposition=f"{target.value} {latest.action} on {latest.resource or '*'} with_proof {pt}",
                )
                # _SITE: completion_reporter
                ce = ConsequenceExecutionEvent(
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
                    completion_proof_type=pt,
                    error_summary=error_summary,
                    proof_subject_hash=proof_hash,
                )
                db.add(ce)
                db.commit()
            except Exception as exc:
                db.rollback()
                print(f"completion_reporter: append failed: {exc}")

        binding = ExecutionBinding(
            token,
            DatabaseAuditSink(self._db(), row.owner_workspace, None),
            cappo_evaluator=cappo_evaluator,
            begin_consequence=begin_consequence,
            completion_reporter=completion_reporter,
        )

        if row.terminated:
            binding._terminated = True
        return MountRecord(
            mount,
            token,
            binding,
            AnchorResult(row.anchor_status, row.anchor_id, row.anchor_detail),
        )

    @staticmethod
    def _owned_by(
        row: CapabilityMount,
        owner_principal: str,
        owner_workspace: str | None,
    ) -> bool:
        if row.owner_principal != owner_principal:
            return False
        if owner_principal == "auth-disabled":
            return True
        return bool(owner_workspace) and row.owner_workspace == owner_workspace

    def _row(self, mount_id: str, *, lock: bool = False) -> CapabilityMount | None:
        statement = select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        if lock:
            statement = statement.with_for_update()
        return self._db().execute(statement).scalar_one_or_none()

    def _evidence_consumed(self, jti: str) -> bool:
        statement = select(CapabilityEvidenceConsumption.jti).where(
            CapabilityEvidenceConsumption.jti == jti
        )
        return self._db().execute(statement).scalar_one_or_none() is not None

    def request_mount(
        self,
        package_ref: str,
        scope: MountScope,
        *,
        role: str,
        policy: MountPolicy,
        ttl_seconds: int,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
        execution_id: str | None = None,
        caller_spiffe_id: str | None = None,
        executor_spiffe_id: str | None = None,
    ) -> tuple[MountRecord | None, AnchorResult, str]:
        db = self._db()
        package = self.packages.get(package_ref)
        if package is None:
            anchor = self.anchor.anchor(
                "mount",
                action="mount",
                decision=Decision.DENY.value,
                reason="unknown_package",
                mount=None,
                token=None,
            )
            db.commit()
            return None, anchor, "unknown_package"
        try:
            mount, token = self.mounter.mount(
                package,
                scope,
                policy,
                ttl=ttl_seconds,
                role=role,
                execution_id=execution_id,
            )
            
            if caller_spiffe_id:
                from cappo_backend.security.biscuit import mint_biscuit_capability
                
                revocation_scope = f"execution:{token.execution_id}"
                revocation_epoch = 0

                biscuit_token = mint_biscuit_capability(
                    caller_spiffe_id=caller_spiffe_id,
                    executor_spiffe_id=executor_spiffe_id,
                    capability_id=package.id,
                    reads=token.grants.reads,
                    writes=token.grants.writes,
                    execution_id=token.execution_id,
                    ttl_seconds=token.ttl_seconds,
                    revocation_scope=revocation_scope,
                    revocation_epoch=revocation_epoch,
                )
                token = token.model_copy(update={"biscuit_token": biscuit_token})
                
        except MountError as exc:
            return None, AnchorResult("not_applicable"), str(exc)

        anchor = self.anchor.anchor(
            "mount",
            action="mount",
            decision=Decision.ALLOW.value,
            reason="mounted",
            mount=mount,
            token=token,
        )
        if anchor.status not in ("confirmed", "pending_reconciliation"):
            db.rollback()
            return None, anchor, "pgl_anchor_unconfirmed"

        db.add(
            CapabilityMount(
                mount_id=mount.id,
                token_id=token.token_id,
                token_nonce=token.nonce,
                owner_principal=owner_principal,
                owner_workspace=owner_workspace or scope.workspace,
                mount_json=mount.model_dump(mode="json"),
                token_json=token.model_dump(mode="json"),
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                anchor_status=anchor.status,
                anchor_id=anchor.anchor_id,
                anchor_detail=anchor.detail,
            )
        )
        
        from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
        
        _biscuit_sha256 = "none"
        if token.biscuit_token:
            import hashlib
            _biscuit_sha256 = hashlib.sha256(token.biscuit_token.encode()).hexdigest()
            
        lease = CapabilityLease(
            lease_id=f"lease_{mount.id}",
            mount_id=mount.id,
            capability_id=package.id,
            policy_version="1.0",
            execution_identity=token.execution_id or "unknown",
            subject_spiffe_id=caller_spiffe_id or "legacy-unbound",
            executor_spiffe_id=executor_spiffe_id or "legacy-unbound",
            biscuit_hash=_biscuit_sha256,
            issued_at=token.issued_at,
            not_before=token.issued_at,
            expires_at=token.expires_at,
            lease_state=LeaseState.ACTIVE.value,
            allowed_actions=set(token.grants.reads + token.grants.writes + token.grants.external_send),
            allowed_resources=set(["*"]),
            offline_enabled=False,
            revocation_scope=f"execution:{token.execution_id}" if token.execution_id else "workspace"
        )
        db.add(lease)
        
        db.commit()
        return (
            MountRecord(mount, token, ExecutionBinding(token, DatabaseAuditSink(db, scope.workspace, None))),
            anchor,
            "mounted",
        )

    def get(self, mount_id: str) -> MountRecord | None:
        row = self._row(mount_id)
        if row is None:
            return None
        return self._record(row)

    def status(
        self,
        mount_id: str,
        *,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
    ) -> tuple[MountRecord | None, str]:
        row = self._row(mount_id)
        if row is None:
            return None, "unknown_mount"
        if not self._owned_by(row, owner_principal, owner_workspace):
            return None, "owner_mismatch"
        record = self._record(row)
        if row.terminated:
            state = "terminated"
        elif _utc(row.expires_at) <= utc_now():
            state = "expired"
        else:
            state = "mounted"
        return record, state

    def evaluate(
        self,
        mount_id: str,
        action: str,
        *,
        resource: str | None = None,
        token_id: str,
        nonce: str,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
        approval_token: str | None = None,
        suppression_evidence: str | None = None,
        suppression_confirmed: bool = False,
        spiffe_fields: dict[str, str | None] | None = None,
    ) -> tuple[Decision, str, AnchorResult, ExecutionBinding | None]:
        # ``suppression_confirmed`` remains a compatibility input only. A caller
        # boolean is never evidence and cannot satisfy the suppression gate.
        _ = suppression_confirmed
        db = self._db()
        row = self._row(mount_id, lock=True)
        if row is None:
            anchor = self.anchor.anchor(
                "action_decision",
                principal=owner_principal,
                mount_id=mount_id,
                timestamp=utc_now().isoformat(),
                action=action,
                decision=Decision.DENY.value,
                reason="unknown_mount",
                mount=None,
                token=None,
            )
            db.commit()
            return Decision.DENY, "unknown_mount", anchor, None
        if not self._owned_by(row, owner_principal, owner_workspace):
            anchor = self.anchor.anchor(
                "action_decision",
                principal=owner_principal,
                mount_id=mount_id,
                timestamp=utc_now().isoformat(),
                action=action,
                decision=Decision.DENY.value,
                reason="owner_mismatch",
                mount=None,
                token=None,
            )
            db.commit()
            return Decision.DENY, "owner_mismatch", anchor, None

        record = self._record(row)
        if row.terminated:
            reason = "terminated"
        elif _utc(row.expires_at) <= utc_now():
            reason = "token_expired"
        elif row.nonce_consumed or token_id != row.token_id or nonce != row.token_nonce:
            reason = (
                "token_replay"
                if row.nonce_consumed and token_id == row.token_id and nonce == row.token_nonce
                else "token_mismatch"
            )
        else:
            reason = ""

        # Check budget before continuing
        if not reason:
            from cappo_backend.models.capability_lease import CapabilityLease
            lease = db.execute(select(CapabilityLease).where(CapabilityLease.mount_id == mount_id)).scalar_one_or_none()
            if lease and lease.offline_enabled and lease.offline_budget is not None and lease.offline_budget <= 0:
                reason = "offline_budget_exhausted"

        if reason:
            anchor = self.anchor.anchor(
                "action_decision",
                principal=owner_principal,
                mount_id=mount_id,
                timestamp=utc_now().isoformat(),
                action=action,
                decision=Decision.DENY.value,
                reason=reason,
                mount=record.mount,
                token=record.token,
            )
            db.commit()
            return Decision.DENY, reason, anchor, None

        # ARCH-P2: Enforce CapabilityLease cryptographic authority subset semantics.
        # Authority must be extracted from the actual Biscuit token, not reconstructed from metadata.
        from cappo_backend.models.capability_lease import (
            AuthorityContext,
            CapabilityLease,
            ConnectivityState,
            InvariantViolationError,
        )
        lease = db.execute(select(CapabilityLease).where(CapabilityLease.mount_id == mount_id)).scalar_one_or_none()
        if lease:
            try:
                # --- Real Biscuit authority extraction ---
                b_auth: AuthorityContext | None = None
                if record.token.biscuit_token:
                    from cappo_backend.security.biscuit import extract_authority_context
                    b_auth = extract_authority_context(record.token.biscuit_token)
                else:
                    # P3 GOVERNED BOUNDARY ENFORCEMENT
                    # Metadata alone cannot authorize without Biscuit. 
                    # We fail closed if there is no cryptographic authority.
                    anchor = self.anchor.anchor(
                        "action_decision",
                        principal=owner_principal,
                        mount_id=mount_id,
                        timestamp=utc_now().isoformat(),
                        action=action,
                        decision=Decision.DENY.value,
                        reason="missing_cryptographic_authority",
                        mount=record.mount,
                        token=record.token,
                    )
                    db.commit()
                    return Decision.DENY, "missing_cryptographic_authority", anchor, None

                # --- Real CapabilityPackage ceiling ---
                package = self.packages.get(record.token.package_ref)
                if package is not None:
                    package_actions = set(package.reads + package.writes + package.external_send_actions)
                    p_auth = AuthorityContext(
                        allowed_actions=package_actions,
                        allowed_resources={"*"},
                        executor_spiffe_id=b_auth.executor_spiffe_id,
                        expires_at=b_auth.expires_at,
                        delegation_depth=b_auth.delegation_depth,
                        max_delegation_depth=b_auth.max_delegation_depth,
                        authority_epoch=b_auth.authority_epoch,
                    )
                else:
                    # No package found — use Biscuit authority as the ceiling (narrowest safe default)
                    p_auth = b_auth

                # This throws InvariantViolationError if any subset invariant is breached
                effective_auth = lease.evaluate_authority(b_auth, p_auth, ConnectivityState.ONLINE)
                if action not in effective_auth.allowed_actions:
                    raise InvariantViolationError(f"Action '{action}' is not in effective authority subset: {effective_auth.allowed_actions}")

            except InvariantViolationError as e:
                print(f"DEBUG INVARIANT: {e}")
                reason = "lease_invariant_violation"
                anchor = self.anchor.anchor(
                    "action_decision",
                    principal=owner_principal,
                    mount_id=mount_id,
                    timestamp=utc_now().isoformat(),
                    action=action,
                    decision=Decision.DENY.value,
                    reason=reason,
                    mount=record.mount,
                    token=record.token,
                )
                db.commit()
                return Decision.DENY, reason, anchor, None

        verified_evidence: list[VerifiedMountEvidence] = []
        try:
            record.binding.check_live()
            if not record.binding._profile.allows(action):  # noqa: SLF001
                reason = (
                    "blocked_action"
                    if record.binding._profile.is_blocked(action)  # noqa: SLF001
                    else "not_in_capability_profile"
                )
                record.binding._append(action, Decision.DENY, reason)  # noqa: SLF001
                decision = Decision.DENY
            else:
                decision = Decision.ALLOW
                reason = "allowed"

                if (
                    action in record.token.grants.external_send
                    and record.token.policy.require_human_approval_for_external_send
                ):
                    approval, _approval_reason = self.evidence_verifier.verify(
                        approval_token,
                        kind="human_approval",
                        principal=owner_principal,
                        mount=record.mount,
                        action=action,
                        nonce=nonce,
                    )
                    if approval is None:
                        decision, reason = Decision.DENY, "human_approval_not_verified"
                    elif self._evidence_consumed(approval.jti):
                        decision, reason = Decision.DENY, "human_approval_replayed"
                    else:
                        verified_evidence.append(approval)

                if (
                    decision is Decision.ALLOW
                    and action in record.token.grants.suppression_required
                    and record.token.policy.require_suppression_check
                ):
                    suppression, _suppression_reason = self.evidence_verifier.verify(
                        suppression_evidence,
                        kind="suppression_check",
                        principal=owner_principal,
                        mount=record.mount,
                        action=action,
                        nonce=nonce,
                    )
                    if suppression is None:
                        decision, reason = Decision.DENY, "suppression_not_verified"
                    elif self._evidence_consumed(suppression.jti):
                        decision, reason = Decision.DENY, "suppression_evidence_replayed"
                    else:
                        verified_evidence.append(suppression)

                if decision is Decision.DENY:
                    record.binding._append(action, Decision.DENY, reason)  # noqa: SLF001
        except TokenExpiredError:
            decision, reason = Decision.DENY, "token_expired"
        except ExecutionTerminatedError:
            decision, reason = Decision.DENY, "terminated"
        except PolicyError as exc:
            decision, reason = Decision.DENY, str(exc)
        if row.terminated:
            decision, reason = Decision.DENY, "terminated"

        # Find the active lease, if any
        from cappo_backend.models.capability_lease import CapabilityLease
        lease = db.query(CapabilityLease).filter(CapabilityLease.mount_id == mount_id).first()
        if lease:
            # Check expiry
            if lease.expires_at and _utc(lease.expires_at) < utc_now():
                decision, reason = Decision.DENY, "lease_expired"

        # Apply offline enforcement if we are operating in disconnected mode
        from cappo_backend.services.pgl_adapter import VeklomPGLAdapter
        is_offline = False
        if isinstance(self.anchor, VeklomPGLAdapter):
            # Check if the PGL anchor is physically unreachable
            is_offline = not self.anchor.is_online()
            
        if is_offline and lease and decision is Decision.ALLOW:
            if not lease.offline_enabled:
                decision, reason = Decision.DENY, "offline_execution_disabled"
            elif lease.offline_budget <= 0:
                decision, reason = Decision.DENY, "offline_budget_exhausted"
            elif lease.offline_side_effect_limit <= 0:
                decision, reason = Decision.DENY, "offline_side_effect_limit_exhausted"
            else:
                # Decrement the limits (enforcement)
                lease.offline_budget -= 1
                lease.offline_side_effect_limit -= 1
                db.add(lease)

        if decision is Decision.ALLOW:
            for evidence in verified_evidence:
                db.add(
                    CapabilityEvidenceConsumption(
                        jti=evidence.jti,
                        kind=evidence.kind,
                        mount_id=mount_id,
                        action=action,
                    )
                )

        anchor = self.anchor.anchor(
            "action_decision",
                principal=owner_principal,
                mount_id=mount_id,
                timestamp=utc_now().isoformat(),
            action=action,
            decision=decision.value,
            reason=reason,
            mount=record.mount,
            token=record.token,
        )
        if decision is Decision.ALLOW and anchor.status not in ("confirmed", "pending_reconciliation"):
            db.rollback()
            return Decision.DENY, "pgl_anchor_unconfirmed", anchor, None
        receipt: CapabilityActionReceipt | None = None
        if decision is Decision.ALLOW:
            row.nonce_consumed = True
            _actioned_at = utc_now()
            _biscuit_sha256 = None
            if record.token.biscuit_token:
                import hashlib
                _biscuit_sha256 = hashlib.sha256(record.token.biscuit_token.encode()).hexdigest()

            sp = spiffe_fields or {}
            
            _receipt_canonical = {
                "execution_id": record.token.execution_id,
                "mount_id": mount_id,
                "token_id": record.token.token_id,
                "principal": owner_principal,
                "caller_spiffe_id": sp.get("caller_spiffe_id"),
                "executor_spiffe_id": sp.get("executor_spiffe_id"),
                "eei_id": sp.get("eei_id"),
                "profile_id": sp.get("profile_id"),
                "lease_id": sp.get("lease_id"),
                "operator_id": sp.get("operator_id"),
                "caller_cert_sha256": sp.get("caller_cert_sha256"),
                "capability_id": record.mount.package_ref,
                "biscuit_token_sha256": _biscuit_sha256,
                "action": action,
                "resource": "*",
                "policy_version": "1.0",
                "decision": decision.value,
                "reason": reason,
                "timestamp": _actioned_at.isoformat(),
                "actioned_at": _actioned_at.isoformat(),
                "result_hash": None,
                "pgl_anchor_id": anchor.anchor_id,
            }
            
            from cappo_backend.security.evidence import (
                get_evidence_key_pair,
                mint_signed_execution_evidence,
            )
            _evidence_pk = get_evidence_key_pair()
            _cose_bytes = mint_signed_execution_evidence(_receipt_canonical, _evidence_pk)
            
            receipt = CapabilityActionReceipt(
                receipt_id=f"rcpt_{anchor.anchor_id or utc_now().strftime('%Y%m%d%H%M%S%f')}",
                execution_id=record.token.execution_id,
                mount_id=mount_id,
                token_id=record.token.token_id,
                principal=owner_principal,
                action=action,
                decision=decision.value,
                reason=reason,
                actioned_at=_actioned_at,
                content_hash=sha256_json(_receipt_canonical),
                pgl_anchor_id=anchor.anchor_id,
                caller_spiffe_id=sp.get("caller_spiffe_id"),
                executor_spiffe_id=sp.get("executor_spiffe_id"),
                eei_id=sp.get("eei_id"),
                profile_id=sp.get("profile_id"),
                lease_id=sp.get("lease_id"),
                operator_id=sp.get("operator_id"),
                caller_cert_sha256=sp.get("caller_cert_sha256"),
                capability_id=record.mount.package_ref,
                trust_domain=sp.get("trust_domain"),
                svid_not_before=sp.get("svid_not_before"),
                svid_not_after=sp.get("svid_not_after"),
                policy_version="1.0",
                biscuit_token_sha256=_biscuit_sha256,
                signed_receipt_cose=_cose_bytes,
            )
            # NOTE: Consequence lifecycle (AUTHORIZED→STARTED→SUCCEEDED|FAILED|OUTCOME_UNKNOWN)
            # is tracked via ConsequenceExecutionEvent append-only events, NOT on this receipt.
            # This receipt is immutable authorization evidence only.
            db.add(receipt)
            from cappo_backend.security.merkle_ops import assign_merkle_leaf_index
            assign_merkle_leaf_index(db, receipt)
        db.commit()
        if decision is Decision.ALLOW:
            record.binding._append(action, Decision.ALLOW, reason)  # noqa: SLF001
        return (
            decision,
            reason,
            anchor,
            {
                "mount_id": mount_id,
                "action": action,
                "decision": decision.value,
                "reason": reason,
                "receipt_id": receipt.receipt_id if decision is Decision.ALLOW else None,
            },
        )

    def execute_consequence(
        self,
        mount_id: str,
        action: str,
        *,
        token_id: str,
        nonce: str,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
        approval_token: str | None = None,
        suppression_evidence: str | None = None,
        suppression_confirmed: bool = False,
        target_ref: str,
        resource: str,
        arguments: dict[str, Any],
        operation_id: str | None = None,
    ) -> tuple[Decision, str, str | None, dict[str, Any]]:
        """Authorize and execute one registered capability consequence."""
        db = self._db()
        op_id = operation_id or str(uuid4())

        def payload(
            *,
            record: MountRecord | None,
            decision: Decision,
            reason: str,
            consequence_state: str | None = None,
            target_invoked: bool = False,
            resulting_state: object | None = None,
            receipt_id: str | None = None,
            terminated: bool = False,
        ) -> dict[str, Any]:
            return {
                "mount_id": mount_id,
                "execution_id": record.token.execution_id if record else None,
                "operation_id": op_id,
                "action": action,
                "target_ref": target_ref,
                "resource": resource,
                "decision": decision.value,
                "reason": reason,
                "consequence_state": consequence_state,
                "target_invoked": target_invoked,
                "resulting_state": resulting_state,
                "receipt_id": receipt_id,
                "nonce_consumed": bool(row.nonce_consumed) if record else None,
                "terminated": terminated,
            }

        def terminal_deny(
            reason: str,
            record: MountRecord,
        ) -> tuple[Decision, str, str | None, dict[str, Any]]:
            termination, _termination_reason, _termination_anchor = self.terminate(
                mount_id,
                UnmountReason.TASK_COMPLETE,
                owner_principal=owner_principal,
                owner_workspace=owner_workspace,
            )
            terminated = termination is Decision.ALLOW
            return (
                Decision.DENY,
                reason,
                None,
                payload(
                    record=record,
                    decision=Decision.DENY,
                    reason=reason,
                    terminated=terminated,
                ),
            )

        def preflight_deny(
            reason: str,
            record: MountRecord | None,
        ) -> tuple[Decision, str, str | None, dict[str, Any]]:
            # Preflight failures must NOT consume the nonce or terminate the mount.
            return (
                Decision.DENY,
                reason,
                None,
                payload(
                    record=record,
                    decision=Decision.DENY,
                    reason=reason,
                    terminated=False,
                ),
            )

        row = self._row(mount_id, lock=True)
        if row is None:
            return preflight_deny("unknown_mount", None)
        if not self._owned_by(row, owner_principal, owner_workspace):
            return preflight_deny("owner_mismatch", None)

        record = self._record(row)
        if (
            not row.terminated
            and _utc(row.expires_at) > utc_now()
            and (token_id != row.token_id or nonce != row.token_nonce)
        ):
            return preflight_deny("token_mismatch", record)
        adapter = self.effect_targets.resolve(target_ref)
        if adapter is None:
            return preflight_deny("unknown_effect_target", record)
        if action not in adapter.actions:
            return preflight_deny("effect_not_mapped", record)
        try:
            validate_resource(resource)
        except ValueError:
            return preflight_deny("invalid_effect_resource", record)

        if operation_id is not None:
            from cappo_backend.models.consequence_execution import build_intent_hash

            normalized_args = {
                key: str(value)
                for key, value in {
                    "resource": resource,
                    "arguments": arguments,
                    "target_ref": target_ref,
                    "approval_token": approval_token,
                    "suppression_evidence": suppression_evidence,
                    "suppression_confirmed": suppression_confirmed,
                }.items()
                if key not in ("approval_token", "suppression_evidence", "suppression_confirmed")
            }
            expected_intent = build_intent_hash(
                mount_id=record.mount.id,
                execution_id=record.token.execution_id,
                action=action,
                resource=resource,
                normalized_args=normalized_args,
            )
            latest = db.execute(
                select(ConsequenceExecutionEvent)
                .where(ConsequenceExecutionEvent.operation_id == operation_id)
                .order_by(ConsequenceExecutionEvent.version.desc())
                .limit(1)
            ).scalar_one_or_none()
            if latest is not None:
                replay_reason = (
                    "idempotency_intent_mismatch"
                    if latest.intent_hash != expected_intent
                    else f"idempotency_replay:{latest.state}"
                )
                return (
                    Decision.DENY,
                    replay_reason,
                    latest.state,
                    payload(
                        record=record,
                        decision=Decision.DENY,
                        reason=replay_reason,
                        consequence_state=latest.state,
                        receipt_id=latest.receipt_id,
                    ),
                )

        before_count = adapter.invocations_by_action.get(action, 0)
        result: object | None = None
        decision = Decision.ALLOW
        reason = "allowed"
        passthrough = {
            "resource": resource,
            "arguments": arguments,
            "target_ref": target_ref,
            "approval_token": approval_token,
            "suppression_evidence": suppression_evidence,
            "suppression_confirmed": suppression_confirmed,
        }

        def invoke_effect(**_: object) -> object:
            return adapter.invoke(action, resource, arguments)

        try:
            # CAPPO's binding is the sole owner of authorization and execution.
            result = record.binding.consequence(
                action,
                invoke_effect,
                operation_id=op_id,
                **passthrough,
            )
        except PolicyError as exc:
            decision = Decision.DENY
            reason = str(exc)
        except Exception:
            # The durable consequence event is the source of truth for outcome.
            decision = Decision.ALLOW

        latest = db.execute(
            select(ConsequenceExecutionEvent)
            .where(ConsequenceExecutionEvent.operation_id == op_id)
            .order_by(ConsequenceExecutionEvent.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        consequence_state = latest.state if latest is not None else None
        receipt_id = latest.receipt_id if latest is not None else None
        after_count = adapter.invocations_by_action.get(action, 0)
        target_invoked = after_count > before_count

        should_terminate = not (
            reason.startswith("idempotency_replay:")
            or reason == "consequence_already_started"
        )
        terminated = False
        if should_terminate:
            termination, _termination_reason, _termination_anchor = self.terminate(
                mount_id,
                UnmountReason.TASK_COMPLETE,
                owner_principal=owner_principal,
                owner_workspace=owner_workspace,
            )
            terminated = termination is Decision.ALLOW

        return (
            decision,
            reason,
            consequence_state,
            payload(
                record=record,
                decision=decision,
                reason=reason,
                consequence_state=consequence_state,
                target_invoked=target_invoked,
                resulting_state=result,
                receipt_id=receipt_id,
                terminated=terminated,
            ),
        )


    def terminate(
        self,
        mount_id: str,
        reason: UnmountReason,
        *,
        owner_principal: str = "auth-disabled",
        owner_workspace: str | None = None,
    ) -> tuple[Decision, str, AnchorResult]:
        db = self._db()
        row = self._row(mount_id, lock=True)
        if row is None:
            anchor = self.anchor.anchor(
                "terminate",
                action="execution",
                decision=Decision.DENY.value,
                reason="unknown_mount",
                mount=None,
                token=None,
            )
            db.commit()
            return Decision.DENY, "unknown_mount", anchor
        if not self._owned_by(row, owner_principal, owner_workspace):
            anchor = self.anchor.anchor(
                "terminate",
                action="execution",
                decision=Decision.DENY.value,
                reason="owner_mismatch",
                mount=None,
                token=None,
            )
            db.commit()
            return Decision.DENY, "owner_mismatch", anchor

        record = self._record(row)
        if row.terminated:
            db.commit()
            return (
                Decision.ALLOW,
                "already_terminated",
                AnchorResult("not_applicable", detail="already terminated"),
            )
        anchor = self.anchor.anchor(
            "terminate",
            action="execution",
            decision=Decision.ALLOW.value,
            reason=reason.value,
            mount=record.mount,
            token=record.token,
        )
        if anchor.status not in ("confirmed", "pending_reconciliation"):
            db.rollback()
            return Decision.DENY, "pgl_anchor_unconfirmed", anchor
            
        row.terminated = True
        mark_mount_revoked(mount_id)
        
        from cappo_backend.models.capability_lease import CapabilityLease, LeaseState
        lease = db.query(CapabilityLease).filter(CapabilityLease.mount_id == mount_id).first()
        if lease and lease.lease_state in (LeaseState.ISSUED.value, LeaseState.ACTIVE.value):
            current_epoch = lease.revocation_epoch + 1
            # "terminate" explicitly means stopping it before natural expiration, 
            # so we use REVOKED and bump the revocation epoch.
            lease.transition_state(LeaseState.REVOKED, current_epoch)

        db.commit()
        return Decision.ALLOW, "terminated", anchor


def load_packages_from_json(raw: str | None) -> list[CapabilityPackage]:
    """Load explicitly configured packages; an absent catalog is intentionally empty."""
    if not raw:
        return []
    import json

    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("CAPPO_CAPABILITY_PACKAGES_JSON must be a JSON list")
    return [CapabilityPackage.model_validate(item) for item in value]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
