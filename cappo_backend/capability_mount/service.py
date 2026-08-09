"""Durable capability mount lifecycle service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.models.capability_mount import CapabilityMount

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
    """Owns package discovery and DB-backed ephemeral mount records."""

    def __init__(self, db: Session | None = None, anchor: EventAnchor | None = None) -> None:
        self.db = db
        self.packages: dict[str, CapabilityPackage] = {}
        self.anchor = anchor or UnconfirmedAnchor()
        self.mounter = Mounter()
        self._records: dict[str, MountRecord] = {}

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
        token = EphemeralScopedToken.model_validate(row.token_json)
        return MountRecord(
            mount,
            token,
            ExecutionBinding(token, InMemoryAuditSink()),
            AnchorResult(row.anchor_status, row.anchor_id, row.anchor_detail),
        )

    def _row(self, mount_id: str, *, lock: bool = False) -> CapabilityMount | None:
        statement = select(CapabilityMount).where(CapabilityMount.mount_id == mount_id)
        if lock:
            statement = statement.with_for_update()
        return self._db().execute(statement).scalar_one_or_none()

    def request_mount(
        self,
        package_ref: str,
        scope: MountScope,
        *,
        role: str,
        policy: MountPolicy,
        ttl_seconds: int,
        execution_id: str | None = None,
    ) -> tuple[MountRecord | None, AnchorResult, str]:
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
            return None, anchor, "pgl_anchor_unconfirmed"

        db = self._db()
        db.add(
            CapabilityMount(
                mount_id=mount.id,
                token_id=token.token_id,
                token_nonce=token.nonce,
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
        record = MountRecord(mount, token, ExecutionBinding(token, InMemoryAuditSink()))
        self._records[mount.id] = record
        return record, anchor, "mounted"

    def get(self, mount_id: str) -> MountRecord | None:
        if mount_id in self._records:
            return self._records[mount_id]
        row = self._row(mount_id)
        if row is None:
            return None
        record = self._records.get(mount_id) or self._record(row)
        self._records[mount_id] = record
        return record

    def evaluate(
        self,
        mount_id: str,
        action: str,
        *,
        token_id: str,
        nonce: str,
        approval_token: str | None = None,
        suppression_confirmed: bool = False,
    ) -> tuple[Decision, str, AnchorResult, dict[str, Any] | None]:
        db = self._db()
        row = self._row(mount_id, lock=True)
        if row is None:
            anchor = self.anchor.anchor(
                "action_decision",
                action=action,
                decision=Decision.DENY.value,
                reason="unknown_mount",
                mount=None,
                token=None,
            )
            db.commit()
            return Decision.DENY, "unknown_mount", anchor, None
        record = self._records.get(mount_id) or self._record(row)
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
                action=action,
                decision=Decision.DENY.value,
                reason=reason,
                mount=record.mount,
                token=record.token,
            )
            db.commit()
            return Decision.DENY, reason, anchor, None
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
            elif (
                action in record.token.grants.external_send
                and record.token.policy.require_human_approval_for_external_send
                and not approval_token
            ):
                reason = "human_approval_required"
                record.binding._append(action, Decision.DENY, reason)  # noqa: SLF001
                decision = Decision.DENY
            elif (
                action in record.token.grants.suppression_required
                and record.token.policy.require_suppression_check
                and suppression_confirmed is not True
            ):
                reason = "suppression_check_required"
                record.binding._append(action, Decision.DENY, reason)  # noqa: SLF001
                decision = Decision.DENY
            else:
                decision = Decision.ALLOW
                reason = "allowed"
        except TokenExpiredError:
            decision, reason = Decision.DENY, "token_expired"
        except ExecutionTerminatedError:
            decision, reason = Decision.DENY, "terminated"
        except PolicyError as exc:
            decision, reason = Decision.DENY, str(exc)

        anchor = self.anchor.anchor(
            "action_decision",
            action=action,
            decision=decision.value,
            reason=reason,
            mount=record.mount,
            token=record.token,
        )
        if decision is Decision.ALLOW and anchor.status != "confirmed":
            db.commit()
            return Decision.DENY, "pgl_anchor_unconfirmed", anchor, None
        if decision is Decision.ALLOW:
            row.nonce_consumed = True
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

    def terminate(self, mount_id: str, reason: UnmountReason) -> tuple[Decision, str, AnchorResult]:
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
            db.commit()
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
