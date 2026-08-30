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
        cappo_evaluator: Callable[..., tuple[Decision, str, str | None]] | None = None,
        begin_consequence: Callable[[str], bool] | None = None,
        completion_reporter: Callable[..., None] | None = None,
    ) -> None:
        self.token = token
        self.sink = sink or InMemoryAuditSink()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._terminated = False
        self._last_hash: str | None = None
        self._profile = CapabilityProfile(token.grants)
        # P5 bridge callables — injected by MountRegistry._record()
        self._cappo_evaluator = cappo_evaluator
        self._begin_consequence = begin_consequence
        self._completion_reporter = completion_reporter

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

    def evaluate_pure(self, action: str, **kwargs: object) -> None:
        """Evaluate capability bounds for a pure local action.
        Does NOT execute any arbitrary callback, structurally preventing side-effect bypasses.
        Does not consume nonces, budgets, or PGL receipts."""
        self._local_eval(action, kwargs)
        self._append(action, Decision.ALLOW, "allowed")

    def consequence(
        self,
        action: str,
        fn: Action[T],
        *,
        operation_id: str | None = None,
        **kwargs: object,
    ) -> T:
        """Execute a state-mutating or externally observable consequence.

        P5 — Truth-State Synchronization:

        The consequence lifecycle is tracked as a separate ConsequenceExecution
        record, entirely distinct from the authorization receipt. These are two
        different facts:

            CapabilityActionReceipt  → CAPPO said yes (immutable, written once)
            ConsequenceExecution     → what actually happened as a result

        State machine driven here:
            (evaluate) → AUTHORIZED   [written inside cappo_evaluator]
            (here)     → STARTED      [written before fn() — crash after this =
                                       OUTCOME_UNKNOWN, not FAILED]
            (here)     → SUCCEEDED    [fn() returned without exception]
            (here)     → FAILED       [fn() raised, outcome provably failed]
            (here)     → OUTCOME_UNKNOWN [fn() raised after ambiguous side effect]

        OUTCOME_UNKNOWN is the correct state when Veklom cannot distinguish
        "consequence happened and callback raised" from "consequence did not happen."
        Never blindly write FAILED in that case.

        Args:
            action:       The governed action string.
            fn:           The consequence callable — the real side effect.
            operation_id: Optional caller-supplied idempotency key. If None, a
                          fresh UUID4 is generated. Reuse with the same intent
                          returns the cached state. Reuse with different intent
                          raises PolicyError("idempotency_intent_mismatch").
            **kwargs:     Passed through to fn() and used to compute intent_hash.
        """
        self._local_eval(action, kwargs)

        if not self._cappo_evaluator:
            self._append(action, Decision.DENY, "cappo_evaluator_missing")
            raise PolicyError("cappo_evaluator_missing")

        # Generate stable operation identity for this consequence attempt.
        op_id = operation_id or str(uuid4())

        # Build intent hash from canonical fields. Reuse of op_id with different
        # intent is an idempotency violation and will be denied by the evaluator.
        from cappo_backend.models.consequence_execution import build_intent_hash
        normalized_args = {k: str(v) for k, v in kwargs.items()
                          if k not in ("approval_token", "suppression_evidence", "suppression_confirmed")}
        i_hash = build_intent_hash(
            mount_id=self.token.mount_id,
            execution_id=self.token.execution_id,
            action=action,
            resource=str(kwargs.get("resource")) if "resource" in kwargs else None,
            normalized_args=normalized_args,
        )

        decision, reason, receipt_id = self._cappo_evaluator(
            action, kwargs, operation_id=op_id, intent_hash=i_hash
        )
        if decision != Decision.ALLOW:
            self._append(action, decision, reason)
            raise PolicyError(reason)

        # ConsequenceExecution is now in AUTHORIZED state.
        # Claim STARTED ownership before touching fn().
        # If the process crashes after this write but before fn() returns,
        # the record stays in STARTED — the reconciliation scanner will mark
        # it OUTCOME_UNKNOWN, not blindly FAILED.
        owned = True
        if self._begin_consequence:
            try:
                owned = self._begin_consequence(op_id)
            except Exception:
                owned = False  # degraded mode — continue but cannot track state

        if not owned:
            # Another worker already owns this execution. Do not duplicate.
            self._append(action, Decision.DENY, "consequence_already_started")
            raise PolicyError("consequence_already_started")

        # Execute the real consequence.
        # Every branch below must report outcome — success, definite failure, or uncertain.
        try:
            result = fn(**kwargs)
        except Exception as exc:
            # Callback raised. This does NOT guarantee the consequence didn't happen.
            # For example: filesystem write succeeded but fsync raised. For pure
            # in-process Python exceptions before any I/O, FAILED is correct.
            # For ambiguous cases, callers should set outcome_uncertain=True via
            # a CappoUncertainError subclass. Default: treat as FAILED (conservative).
            is_uncertain = type(exc).__name__ == "CappoUncertainError"
            
            if self._completion_reporter:
                try:
                    self._completion_reporter(
                        op_id,
                        succeeded=False,
                        error_summary=f"{type(exc).__name__}: {exc}",
                        proof_type="callback_exception",
                        outcome_uncertain=is_uncertain,
                    )
                except Exception:
                    pass
            self._append(action, Decision.DENY, f"consequence_failed:{type(exc).__name__}")
            raise

        # Consequence returned without exception — write SUCCEEDED.
        if self._completion_reporter:
            try:
                self._completion_reporter(
                    op_id,
                    succeeded=True,
                    proof_type="callback_return",
                )
            except Exception:
                pass  # reporting failure must never undo a real consequence

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
