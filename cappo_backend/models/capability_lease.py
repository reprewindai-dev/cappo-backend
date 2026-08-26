from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Set

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cappo_backend.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeaseState(str, Enum):
    ISSUED = "ISSUED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUSPENDED = "SUSPENDED"


class ConnectivityState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class InvariantViolationError(Exception):
    pass


class AuthorityContext:
    def __init__(
        self,
        allowed_actions: Set[str],
        allowed_resources: Set[str],
        executor_spiffe_id: str,
        expires_at: Optional[datetime],
        delegation_depth: int,
        max_delegation_depth: int,
        authority_epoch: int,
    ):
        self.allowed_actions = allowed_actions
        self.allowed_resources = allowed_resources
        self.executor_spiffe_id = executor_spiffe_id
        self.expires_at = expires_at
        self.delegation_depth = delegation_depth
        self.max_delegation_depth = max_delegation_depth
        self.authority_epoch = authority_epoch


class CapabilityLease(Base):
    __tablename__ = "capability_leases"

    lease_id: Mapped[str] = mapped_column(String, primary_key=True)
    mount_id: Mapped[str] = mapped_column(String, index=True)
    capability_id: Mapped[str] = mapped_column(String, index=True)
    policy_version: Mapped[str] = mapped_column(String)
    
    execution_identity: Mapped[str] = mapped_column(String, index=True)
    subject_spiffe_id: Mapped[str] = mapped_column(String)
    executor_spiffe_id: Mapped[str] = mapped_column(String)
    biscuit_hash: Mapped[str] = mapped_column(String, index=True)
    
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    
    lease_state: Mapped[str] = mapped_column(String, default=LeaseState.ISSUED.value, index=True)
    lease_state_version: Mapped[int] = mapped_column(Integer, default=1)
    
    authority_epoch: Mapped[int] = mapped_column(Integer, default=0)
    revocation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    
    delegation_depth: Mapped[int] = mapped_column(Integer, default=0)
    max_delegation_depth: Mapped[int] = mapped_column(Integer, default=0)
    
    # Store sets as JSON arrays for persistence
    _allowed_actions_json: Mapped[str] = mapped_column("allowed_actions", Text, default="[]")
    _allowed_resources_json: Mapped[str] = mapped_column("allowed_resources", Text, default="[]")
    _contextual_bounds_json: Mapped[str] = mapped_column("contextual_bounds", Text, default="{}")
    
    offline_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    maximum_offline_duration: Mapped[int] = mapped_column(Integer, default=0)
    offline_budget: Mapped[int] = mapped_column(Integer, default=0)
    offline_side_effect_limit: Mapped[int] = mapped_column(Integer, default=0)
    
    last_known_policy_epoch: Mapped[int] = mapped_column(Integer, default=0)
    last_known_revocation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    @property
    def allowed_actions(self) -> Set[str]:
        return set(json.loads(self._allowed_actions_json))

    @allowed_actions.setter
    def allowed_actions(self, value: Set[str]):
        self._allowed_actions_json = json.dumps(list(value))
        
    @property
    def allowed_resources(self) -> Set[str]:
        return set(json.loads(self._allowed_resources_json))

    @allowed_resources.setter
    def allowed_resources(self, value: Set[str]):
        self._allowed_resources_json = json.dumps(list(value))
        
    @property
    def contextual_bounds(self) -> dict:
        return json.loads(self._contextual_bounds_json)

    @contextual_bounds.setter
    def contextual_bounds(self, value: dict):
        self._contextual_bounds_json = json.dumps(value)

    def evaluate_authority(self, biscuit_auth: Optional[AuthorityContext], package_auth: AuthorityContext, connectivity: ConnectivityState) -> AuthorityContext:
        """
        Evaluates the effective authority under the constitutional subset invariant:
        effective_lease_authority <= biscuit_authority <= mount/package_authority
        """
        if self.lease_state not in [LeaseState.ISSUED.value, LeaseState.ACTIVE.value]:
            raise InvariantViolationError(f"Lease cannot authorize in state {self.lease_state}")

        if not biscuit_auth:
            raise InvariantViolationError("METADATA_CAN_AUTHORIZE_WITHOUT_BISCUIT")

        # Invariant checks
        if not self.allowed_actions.issubset(biscuit_auth.allowed_actions):
            raise InvariantViolationError("LEASE_CAN_WIDEN_ACTION")
            
        if not self.allowed_resources.issubset(biscuit_auth.allowed_resources):
            raise InvariantViolationError("LEASE_CAN_WIDEN_RESOURCE")
            
        if self.executor_spiffe_id != biscuit_auth.executor_spiffe_id:
            raise InvariantViolationError("LEASE_CAN_CHANGE_EXECUTOR")
            
        if biscuit_auth.expires_at and self.expires_at > biscuit_auth.expires_at:
            raise InvariantViolationError("LEASE_CAN_EXTEND_EXPIRY")
            
        if self.delegation_depth > biscuit_auth.max_delegation_depth:
            raise InvariantViolationError("LEASE_CAN_INCREASE_DELEGATION")
            
        if self.authority_epoch < biscuit_auth.authority_epoch:
            raise InvariantViolationError("LEASE_CAN_ROLLBACK_AUTHORITY_EPOCH")
            
        if connectivity == ConnectivityState.OFFLINE:
            if not self.offline_enabled:
                raise InvariantViolationError("OFFLINE_MODE_CAN_CREATE_NEW_AUTHORITY")

        effective_actions = self.allowed_actions.intersection(biscuit_auth.allowed_actions).intersection(package_auth.allowed_actions)
        effective_resources = self.allowed_resources.intersection(biscuit_auth.allowed_resources).intersection(package_auth.allowed_resources)
        
        return AuthorityContext(
            allowed_actions=effective_actions,
            allowed_resources=effective_resources,
            executor_spiffe_id=self.executor_spiffe_id,
            expires_at=self.expires_at,
            delegation_depth=self.delegation_depth,
            max_delegation_depth=self.max_delegation_depth,
            authority_epoch=self.authority_epoch
        )

    def transition_state(self, new_state: LeaseState, current_epoch: int):
        if self.lease_state == LeaseState.REVOKED.value and new_state != LeaseState.REVOKED:
            raise InvariantViolationError("REVOKED_LEASE_CAN_RESURRECT")
            
        self.lease_state = new_state.value
        self.lease_state_version += 1
        
    def attenuate(self, new_actions: Set[str], new_resources: Set[str], current_epoch: int):
        if not new_actions.issubset(self.allowed_actions):
            raise InvariantViolationError("CHILD_LEASE_CANNOT_WIDEN_ACTIONS")
        if not new_resources.issubset(self.allowed_resources):
            raise InvariantViolationError("CHILD_LEASE_CANNOT_WIDEN_RESOURCES")
            
        if current_epoch < self.revocation_epoch:
            raise InvariantViolationError("LEASE_CAN_ROLLBACK_REVOCATION_EPOCH")
            
        self.allowed_actions = new_actions
        self.allowed_resources = new_resources
        self.revocation_epoch = current_epoch
        self.lease_state_version += 1
