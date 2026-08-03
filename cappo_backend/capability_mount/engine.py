"""Pure capability mounting and fail-closed execution enforcement."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_hex
from typing import Callable, Protocol, TypeVar
from uuid import uuid4

from .errors import (
    ExecutionTerminatedError,
    MountError,
    PolicyError,
    TokenExpiredError,
)
from .models import (
    CapabilityPackage,
    Decision,
    EphemeralScopedToken,
    ExecutionAuditEvent,
    Grants,
    Lifecycle,
    Mount,
    MountPolicy,
    MountScope,
    MountToken,
    TokenDescriptorScope,
    TokenType,
    UnmountReason,
)

T = TypeVar("T")
Action = Callable[..., T]


class AuditSink(Protocol):
    """Pluggable append-only audit destination."""

    def append(self, event: ExecutionAuditEvent) -> None:
        """Append an event to the sink."""


class InMemoryAuditSink:
    """Default sink for isolated use and deterministic tests."""

    def __init__(self) -> None:
        self.events: list[ExecutionAuditEvent] = []

    def append(self, event: ExecutionAuditEvent) -> None:
        self.events.append(event)

    def verify_chain(self) -> bool:
        previous: str | None = None
        for event in self.events:
            if event.prev_hash != previous:
                return False
            if _event_hash(event) != event.event_hash:
                return False
            previous = event.event_hash
        return True


class CapabilityProfile:
    """An immutable allowlist with absolute blocked-action precedence."""

    def __init__(self, grants: Grants) -> None:
        self._grants = grants

    def allows(self, action: str) -> bool:
        if action in self._grants.blocked:
            return False
        return action in self._grants.reads or action in self._grants.writes

    def is_blocked(self, action: str) -> bool:
        return action in self._grants.blocked


class Mounter:
    """Creates a package-bound mount and a short-lived token descriptor."""

    DEFAULT_TTL_SECONDS = 300
    MAX_TTL_SECONDS = 600

    def mount(
        self,
        package: CapabilityPackage,
        scope: MountScope,
        policy: MountPolicy | None = None,
        ttl: int = DEFAULT_TTL_SECONDS,
        *,
        role: str = "ephemeral_executor",
        execution_id: str | None = None,
    ) -> tuple[Mount, EphemeralScopedToken]:
        if ttl < 1 or ttl > self.MAX_TTL_SECONDS:
            raise MountError(f"ttl must be between 1 and {self.MAX_TTL_SECONDS} seconds")
        selected_policy = policy or MountPolicy()
        if selected_policy.persistent_memory_allowed:
            raise MountError("persistent memory is not permitted for ephemeral mounts")

        package_reads = set(package.reads)
        package_writes = set(package.writes)
        requested_reads = set(scope.reads) if scope.reads is not None else package_reads
        requested_writes = set(scope.writes) if scope.writes is not None else package_writes
        grants = Grants(
            reads=sorted(package_reads & requested_reads),
            writes=sorted(package_writes & requested_writes),
            blocked=sorted(set(package.blocked) | set(scope.blocked)),
            external_send=sorted(
                set(package.external_send_actions) & (package_writes & requested_writes)
            ),
            suppression_required=sorted(
                set(package.suppression_required_actions) & (package_writes & requested_writes)
            ),
        )
        mount_id = f"mnt_{uuid4().hex}"
        resolved_execution_id = execution_id or f"exec_{uuid4().hex}"
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(seconds=ttl)
        token = EphemeralScopedToken(
            token_id=f"tok_{uuid4().hex}",
            mount_id=mount_id,
            execution_id=resolved_execution_id,
            package_ref=package.id,
            scope=TokenDescriptorScope(workspace=scope.workspace, project=scope.project),
            grants=grants,
            policy=selected_policy,
            issued_at=issued_at,
            expires_at=expires_at,
            ttl_seconds=ttl,
            nonce=token_hex(32),
        )
        mount = Mount(
            id=mount_id,
            package_ref=package.id,
            role=role,
            scope=scope,
            token=MountToken(type=TokenType.EPHEMERAL_SCOPED, ttl_seconds=ttl),
            grants=grants,
            policy=selected_policy,
            lifecycle=Lifecycle(),
        )
        return mount, token


class ExecutionBinding:
    """Binds one token to one execution and exposes fail-closed calls."""

    def __init__(
        self,
        token: EphemeralScopedToken,
        sink: AuditSink | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.token = token
        self.sink = sink or InMemoryAuditSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._terminated = False
        self._last_hash: str | None = None
        self._profile = CapabilityProfile(token.grants)

    def check_live(self) -> None:
        if self._terminated:
            self._append("execution", Decision.DENY, "terminated")
            raise ExecutionTerminatedError("execution is terminated")
        if self._clock() > self.token.expires_at:
            self._append("execution", Decision.DENY, "token_expired")
            raise TokenExpiredError("execution token has expired")

    def call(self, action: str, fn: Action[T], **kwargs: object) -> T:
        try:
            self.check_live()
        except PolicyError:
            raise

        if not self._profile.allows(action):
            reason = (
                "blocked_action"
                if self._profile.is_blocked(action)
                else "not_in_capability_profile"
            )
            self._append(action, Decision.DENY, reason)
            raise PolicyError(reason)

        approval_token = kwargs.pop("approval_token", None)
        suppression_confirmed = kwargs.pop("suppression_confirmed", False)
        if (
            action in self.token.grants.external_send
            and self.token.policy.require_human_approval_for_external_send
            and not approval_token
        ):
            self._append(action, Decision.DENY, "human_approval_required")
            raise PolicyError("human_approval_required")
        if (
            action in self.token.grants.suppression_required
            and self.token.policy.require_suppression_check
            and suppression_confirmed is not True
        ):
            self._append(action, Decision.DENY, "suppression_check_required")
            raise PolicyError("suppression_check_required")

        result = fn(**kwargs)
        self._append(action, Decision.ALLOW, "allowed")
        return result

    def terminate(self, reason: UnmountReason = UnmountReason.EXPLICIT_TERMINATE) -> None:
        if reason not in {
            UnmountReason.TASK_COMPLETE,
            UnmountReason.TOKEN_EXPIRY,
            UnmountReason.EXPLICIT_TERMINATE,
        }:
            raise PolicyError("unsupported unmount reason")
        if not self._terminated:
            self._terminated = True
            self._append("execution", Decision.ALLOW, f"terminated:{reason.value}")

    def _append(self, action: str, decision: Decision, reason: str) -> None:
        timestamp = self._clock()
        event = ExecutionAuditEvent(
            event_id=f"evt_{uuid4().hex}",
            execution_id=self.token.execution_id,
            action=action,
            decision=decision,
            reason=reason,
            ts=timestamp,
            prev_hash=self._last_hash,
            event_hash="pending",
        )
        hashed = event.model_copy(update={"event_hash": _event_hash(event)})
        self.sink.append(hashed)
        self._last_hash = hashed.event_hash


def _event_hash(event: ExecutionAuditEvent) -> str:
    payload = {
        "event_id": event.event_id,
        "execution_id": event.execution_id,
        "action": event.action,
        "decision": event.decision.value,
        "reason": event.reason,
        "ts": event.ts.isoformat(),
        "prev_hash": event.prev_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()
