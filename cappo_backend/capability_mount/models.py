"""Typed, language-neutral capability mount contract models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list[str] | dict[str, str]


class ContractModel(BaseModel):
    """Strict contract models reject undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TokenType(str, Enum):
    EPHEMERAL_SCOPED = "ephemeral_scoped"


class LifecycleState(str, Enum):
    MOUNTED = "mounted"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class UnmountReason(str, Enum):
    TASK_COMPLETE = "task_complete"
    TOKEN_EXPIRY = "token_expiry"
    EXPLICIT_TERMINATE = "explicit_terminate"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class CapabilityPackage(ContractModel):
    id: str = Field(pattern=r"^[A-Za-z0-9._-]+@v[0-9]+$")
    family: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    policy_defaults: dict[str, JsonValue] = Field(default_factory=dict)

    external_send_actions: list[str] = Field(default_factory=list)
    suppression_required_actions: list[str] = Field(default_factory=list)

    @field_validator(
        "reads",
        "writes",
        "blocked",
        "outputs",
        "external_send_actions",
        "suppression_required_actions",
    )
    @classmethod
    def unique_actions(cls, actions: list[str]) -> list[str]:
        if len(actions) != len(set(actions)):
            raise ValueError("action lists must not contain duplicates")
        if any(not action for action in actions):
            raise ValueError("action names must not be empty")
        return actions

    @field_validator("external_send_actions", "suppression_required_actions")
    @classmethod
    def classified_actions_must_be_writes(
        cls,
        actions: list[str],
        info: ValidationInfo,
    ) -> list[str]:
        writes = set(info.data.get("writes", []))
        undeclared = set(actions) - writes
        if undeclared:
            raise ValueError(f"classified actions must be declared writes: {sorted(undeclared)}")
        return actions


class MountScope(ContractModel):
    workspace: str = Field(min_length=1)
    project: str = Field(min_length=1)
    reads: list[str] | None = None
    writes: list[str] | None = None
    blocked: list[str] = Field(default_factory=list)


class Grants(ContractModel):
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    blocked: list[str] = Field(default_factory=list)
    external_send: list[str] = Field(default_factory=list)
    suppression_required: list[str] = Field(default_factory=list)


class MountToken(ContractModel):
    type: TokenType = TokenType.EPHEMERAL_SCOPED
    ttl_seconds: int = Field(ge=1, le=600)


class MountPolicy(ContractModel):
    mode: str = Field(default="draft_only", min_length=1)
    default: Literal["deny"] = "deny"
    require_human_approval_for_external_send: bool = True
    require_suppression_check: bool = True
    persistent_memory_allowed: bool = False


class TokenDescriptorScope(ContractModel):
    workspace: str = Field(min_length=1)
    project: str = Field(min_length=1)


class Lifecycle(ContractModel):
    state: LifecycleState = LifecycleState.MOUNTED
    unmount_on: list[UnmountReason] = Field(
        default_factory=lambda: [
            UnmountReason.TASK_COMPLETE,
            UnmountReason.TOKEN_EXPIRY,
            UnmountReason.EXPLICIT_TERMINATE,
        ]
    )


class Mount(ContractModel):
    id: str = Field(min_length=1)
    package_ref: str = Field(min_length=1)
    role: str = Field(min_length=1)
    scope: MountScope
    token: MountToken
    grants: Grants
    policy: MountPolicy
    lifecycle: Lifecycle = Field(default_factory=Lifecycle)


class EphemeralScopedToken(ContractModel):
    token_id: str = Field(min_length=1)
    mount_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    package_ref: str = Field(min_length=1)
    scope: TokenDescriptorScope
    grants: Grants
    policy: MountPolicy
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int = Field(ge=1, le=600)
    single_use: Literal[True] = True
    nonce_consumed: bool = False
    nonce: str = Field(min_length=1)

    @field_validator("expires_at")
    @classmethod
    def expiry_after_issue(cls, value: datetime, info: ValidationInfo) -> datetime:
        issued_at = info.data.get("issued_at")
        if isinstance(issued_at, datetime) and value <= issued_at:
            raise ValueError("expires_at must be after issued_at")
        return value


class ExecutionAuditEvent(ContractModel):
    event_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    decision: Decision
    reason: str = Field(min_length=1)
    ts: datetime
    prev_hash: str | None
    event_hash: str = Field(min_length=1)
