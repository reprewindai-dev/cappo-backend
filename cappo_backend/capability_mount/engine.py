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


_LOCAL_REVOCATIONS: set[str] = set()

def mark_mount_revoked(mount_id: str) -> None:
    _LOCAL_REVOCATIONS.add(mount_id)

def is_mount_revoked(mount_id: str) -> bool:
    return mount_id in _LOCAL_REVOCATIONS


class AuditSink(Protocol):
    def append(self, event: ExecutionAuditEvent) -> None: ...


class InMemoryAuditSink:
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
    """Create a package-bound mount and short-lived token descriptor."""

    DEFAULT_TTL_SECONDS = 300
    MAX_TTL_SECONDS = 600
    _POLICY_KEYS = frozenset(MountPolicy.model_fields)

    @classmethod
    def _effective_policy(
        cls,
        package: CapabilityPackage,
        requested: MountPolicy | None,
    ) -> MountPolicy:
        """Attenuate caller policy against trusted package policy.

        Caller input may strengthen a gate, never weaken package/default human
        approval, suppression, default-deny, mode, or persistent-memory limits.
        Unknown package policy metadata is ignored here rather than being treated
        as a runtime MountPolicy field.
        """
        package_policy_raw = {
            key: value
            for key, value in package.policy_defaults.items()
            if key in cls._POLICY_KEYS
        }
        trusted = MountPolicy.model_validate(package_policy_raw)
        caller = requested or MountPolicy()
        return MountPolicy(
            mode=trusted.mode,
            default="deny",
            require_human_approval_for_external_send=(
                trusted.require_human_approval_for_external_send
                or caller.require_human_approval_for_external_send
            ),
            require_suppression_check=(
                trusted.require_suppression_check or caller.require_suppression_check
            ),
            persistent_memory_allowed=(
                trusted.persistent_memory_allowed and caller.persistent_memory_allowed
            ),
        )

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
        selected_policy = self._effective_policy(package, policy)
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
    """Bind one token to one execution and expose fail-closed calls."""

    def __init__(
        self,
        token: EphemeralScopedToken,
        sink: AuditSink | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        cappo_evaluator: Callable[[str, dict[str, object]], tuple[Decision, str]] | None = None,
    ) -> None:
        self.token = token
        self.sink = sink or InMemoryAuditSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._terminated = False
        self._last_hash: str | None = None
        self._profile = CapabilityProfile(token.grants)
        self._cappo_evaluator = cappo_evaluator

    def check_live(self) -> None:
        if self._terminated or is_mount_revoked(self.token.mount_id):
            self._terminated = True
            self._append("execution", Decision.DENY, "terminated")
            raise ExecutionTerminatedError("execution is terminated")
        if self._clock() > self.token.expires_at:
            self._append("execution", Decision.DENY, "token_expired")
            raise TokenExpiredError("execution token has expired")

    def _local_eval(self, action: str, kwargs: dict[str, object]) -> None:
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

        kwargs.pop("approval_token", None)
        kwargs.pop("suppression_confirmed", False)
        if (
            action in self.token.grants.external_send
            and self.token.policy.require_human_approval_for_external_send
        ):
            self._append(action, Decision.DENY, "human_approval_not_verified")
            raise PolicyError("human_approval_not_verified")
        if (
            action in self.token.grants.suppression_required
            and self.token.policy.require_suppression_check
        ):
            self._append(action, Decision.DENY, "suppression_not_verified")
            raise PolicyError("suppression_not_verified")

    def compute(self, action: str, fn: Action[T], **kwargs: object) -> T:
        """Execute pure local computation without external consequences.
        Applies local capability bounds but does NOT consume nonces, budgets, or PGL receipts."""
        self._local_eval(action, kwargs)
        result = fn(**kwargs)
        self._append(action, Decision.ALLOW, "allowed")
        return result

    def consequence(self, action: str, fn: Action[T], **kwargs: object) -> T:
        """Execute a state-mutating or externally observable consequence.
        Strictly requires CAPPO dominance (nonce consumption, budget check, PGL receipt)
        before allowing the execution to proceed."""
        self._local_eval(action, kwargs)
        
        if not self._cappo_evaluator:
            # Fail closed if CAPPO evaluator is unconfigured
            self._append(action, Decision.DENY, "cappo_evaluator_missing")
            raise PolicyError("cappo_evaluator_missing")
            
        decision, reason = self._cappo_evaluator(action, kwargs)
        if decision != Decision.ALLOW:
            self._append(action, decision, reason)
            raise PolicyError(reason)
            
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
