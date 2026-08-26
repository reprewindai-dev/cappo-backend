"""Durable capability mount lifecycle service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.capability_evidence_consumption import CapabilityEvidenceConsumption
from cappo_backend.models.capability_mount import CapabilityMount
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.mount_evidence import (
    BoundMountEvidenceVerifier,
    VerifiedMountEvidence,
)

from .engine import ExecutionBinding, InMemoryAuditSink, Mounter
from .errors import ExecutionTerminatedError, MountError, PolicyError, TokenExpiredError
from .models import (
    CapabilityPackage,
    Decision,
    EphemeralScopedToken,
    Mount,
    MountPolicy,
    MountScope,
    UnmountReason,
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
    ) -> None:
        self.db = db
        self.packages: dict[str, CapabilityPackage] = {}
        self.anchor = anchor or UnconfirmedAnchor()
        self.evidence_verifier = evidence_verifier or BoundMountEvidenceVerifier()
        self.mounter = Mounter()

    def register_package(self, package: CapabilityPackage) -> None:
        self.packages[package.id] = package

    def list_packages(self) -> list[CapabilityPackage]:
        return sorted(self.packages.values(), key=lambda package: package.id)

    def _db(self) -> Session:
        if self.db is None:
            raise RuntimeError("durable mount storage requires a database session")
        return self.db

    @staticmethod
    def _record(row: CapabilityMount) -> MountRecord:
        mount = Mount.model_validate(row.mount_json)
        token = EphemeralScopedToken.model_validate(row.token_json).model_copy(
            update={"nonce_consumed": row.nonce_consumed}
        )
        return MountRecord(
            mount,
            token,
            ExecutionBinding(token, InMemoryAuditSink()),
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
                token.biscuit_token = mint_biscuit_capability(
                    caller_spiffe_id=caller_spiffe_id,
                    executor_spiffe_id=executor_spiffe_id,
                    capability_id=package.id,
                    reads=token.grants.reads,
                    writes=token.grants.writes,
                    execution_id=token.execution_id,
                    ttl_seconds=token.ttl_seconds,
                )
                
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
        if anchor.status != "confirmed":
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
        db.commit()
        return (
            MountRecord(mount, token, ExecutionBinding(token, InMemoryAuditSink())),
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
        elif row.nonce_consumed or token_id != row.token_id or nonce != row.token_nonce:
            reason = (
                "token_replay"
                if row.nonce_consumed and token_id == row.token_id and nonce == row.token_nonce
                else "token_mismatch"
            )
        else:
            reason = ""
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
        if decision is Decision.ALLOW and anchor.status != "confirmed":
            db.rollback()
            return Decision.DENY, "pgl_anchor_unconfirmed", anchor, None
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
                "executor_spiffe_id": sp.get("caller_spiffe_id"), # For now
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
            
            from cappo_backend.security.evidence import get_evidence_key_pair, mint_signed_execution_evidence
            _evidence_pk = get_evidence_key_pair()
            _cose_bytes = mint_signed_execution_evidence(_receipt_canonical, _evidence_pk)
            
            db.add(
                CapabilityActionReceipt(
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
                    executor_spiffe_id=sp.get("caller_spiffe_id"),
                    caller_cert_sha256=sp.get("caller_cert_sha256"),
                    capability_id=record.mount.package_ref,
                    trust_domain=sp.get("trust_domain"),
                    svid_not_before=sp.get("svid_not_before"),
                    svid_not_after=sp.get("svid_not_after"),
                    policy_version="1.0",
                    biscuit_token_sha256=_biscuit_sha256,
                    signed_receipt_cose=_cose_bytes,
                )
            )
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
            },
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
        if anchor.status != "confirmed":
            db.rollback()
            return Decision.DENY, "pgl_anchor_unconfirmed", anchor
        row.terminated = True
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
