"""MCPAPI v2.0 — Governance Composition Layer.

Python port of the MCPAPI v2.0 governance-layer reference. Provides:

- :class:`PolicyCompositionEngine` — merges system / owner / runtime policies
  (system is most-restrictive and wins), detecting conflicts.
- :class:`TemporalPolicyResolver` — applies time-window / peak-hour policy
  adjustments for the current moment.
- :class:`DelegationChainTracker` — tracks delegation hops with depth limits and
  per-hop trust degradation, and supports revocation.
- :func:`effective_permissions` — final allow/deny + obligations for an
  agent/capability given the composed policy and current trust.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from cappo_backend.services.canonical import sha256_json

Effect = Literal["allow", "deny"]
ConflictSeverity = Literal["low", "medium", "high", "critical"]
ResolutionMethod = Literal[
    "system-wins", "owner-wins", "most-restrictive", "union", "intersection"
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PolicyRule:
    effect: Effect
    description: str = ""


@dataclass
class Policy:
    policy_id: str
    rules: list[PolicyRule] = field(default_factory=list)
    rate_limit: int | None = None
    trust_required: float | None = None
    requires_approval: bool = False

    @property
    def allows(self) -> bool:
        return any(r.effect == "allow" for r in self.rules) and not any(
            r.effect == "deny" for r in self.rules
        )


@dataclass
class PolicyConflict:
    conflict_id: str
    conflict_type: str
    source1: str
    source2: str
    conflicting_field: str
    value1: object
    value2: object
    severity: ConflictSeverity
    resolution: str
    requires_admin_review: bool


@dataclass
class EffectivePolicy:
    allow: bool
    rate_limit: int | None
    trust_required: float
    requires_approval: bool
    immutable: bool


@dataclass
class PolicyComposition:
    composition_id: str
    agent_id: str
    capability_id: str
    timestamp: datetime
    effective_policy: EffectivePolicy
    conflicts_detected: list[PolicyConflict]
    resolution_method: ResolutionMethod
    is_valid: bool
    evidence_hash: str


class PolicyCompositionEngine:
    """Composes system / owner / runtime policies into one effective policy."""

    def compose(
        self,
        agent_id: str,
        capability_id: str,
        *,
        system_policy: Policy | None = None,
        owner_policy: Policy | None = None,
        runtime_policy: Policy | None = None,
    ) -> PolicyComposition:
        conflicts = self._detect_conflicts(system_policy, owner_policy)
        effective = self._merge(system_policy, owner_policy, runtime_policy)
        is_valid = not any(c.severity == "critical" for c in conflicts)

        return PolicyComposition(
            composition_id=str(uuid.uuid4()),
            agent_id=agent_id,
            capability_id=capability_id,
            timestamp=_now(),
            effective_policy=effective,
            conflicts_detected=conflicts,
            resolution_method="most-restrictive",
            is_valid=is_valid,
            evidence_hash=sha256_json(
                {
                    "allow": effective.allow,
                    "rate_limit": effective.rate_limit,
                    "trust_required": effective.trust_required,
                    "requires_approval": effective.requires_approval,
                }
            ),
        )

    def _detect_conflicts(
        self, system_policy: Policy | None, owner_policy: Policy | None
    ) -> list[PolicyConflict]:
        conflicts: list[PolicyConflict] = []
        if system_policy is None or owner_policy is None:
            return conflicts

        if system_policy.allows and not owner_policy.allows:
            conflicts.append(
                PolicyConflict(
                    conflict_id=str(uuid.uuid4()),
                    conflict_type="allow-deny",
                    source1=system_policy.policy_id,
                    source2=owner_policy.policy_id,
                    conflicting_field="effect",
                    value1="allow",
                    value2="deny",
                    severity="high",
                    resolution="System policy takes precedence",
                    requires_admin_review=True,
                )
            )

        if (
            system_policy.rate_limit is not None
            and owner_policy.rate_limit is not None
            and system_policy.rate_limit < owner_policy.rate_limit
        ):
            conflicts.append(
                PolicyConflict(
                    conflict_id=str(uuid.uuid4()),
                    conflict_type="rate-limit-mismatch",
                    source1=system_policy.policy_id,
                    source2=owner_policy.policy_id,
                    conflicting_field="rate_limit",
                    value1=system_policy.rate_limit,
                    value2=owner_policy.rate_limit,
                    severity="medium",
                    resolution="Use most restrictive (system)",
                    requires_admin_review=False,
                )
            )
        return conflicts

    def _merge(
        self,
        system_policy: Policy | None,
        owner_policy: Policy | None,
        runtime_policy: Policy | None,
    ) -> EffectivePolicy:
        layers = [p for p in (runtime_policy, owner_policy, system_policy) if p is not None]

        # Deny wins: a deny rule anywhere blocks; otherwise require an allow.
        any_deny = any(any(r.effect == "deny" for r in p.rules) for p in layers)
        any_allow = any(any(r.effect == "allow" for r in p.rules) for p in layers)
        allow = any_allow and not any_deny

        # Most-restrictive numeric obligations.
        rate_limits = [p.rate_limit for p in layers if p.rate_limit is not None]
        trust_required = [p.trust_required for p in layers if p.trust_required is not None]

        return EffectivePolicy(
            allow=allow,
            rate_limit=min(rate_limits) if rate_limits else None,
            trust_required=max(trust_required) if trust_required else 0.0,
            requires_approval=any(p.requires_approval for p in layers),
            immutable=system_policy is not None,
        )


@dataclass
class TimeWindow:
    name: str
    start_hour: int
    end_hour: int
    rate_limit: int | None = None
    trust_required: float | None = None
    requires_approval: bool = False

    def contains(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        # Overnight window, e.g. 22 -> 6
        return hour >= self.start_hour or hour < self.end_hour


class TemporalPolicyResolver:
    """Applies time-window adjustments to a base effective policy."""

    def __init__(self, windows: list[TimeWindow] | None = None) -> None:
        self._windows = windows or []

    def resolve(self, base: EffectivePolicy, *, at: datetime | None = None) -> EffectivePolicy:
        at = at or _now()
        hour = at.astimezone(timezone.utc).hour
        rate_limit = base.rate_limit
        trust_required = base.trust_required
        requires_approval = base.requires_approval

        for window in self._windows:
            if not window.contains(hour):
                continue
            if window.rate_limit is not None:
                rate_limit = (
                    window.rate_limit if rate_limit is None else min(rate_limit, window.rate_limit)
                )
            if window.trust_required is not None:
                trust_required = max(trust_required, window.trust_required)
            requires_approval = requires_approval or window.requires_approval

        return EffectivePolicy(
            allow=base.allow,
            rate_limit=rate_limit,
            trust_required=trust_required,
            requires_approval=requires_approval,
            immutable=base.immutable,
        )

    @staticmethod
    def business_hours(
        *, start: int = 9, end: int = 17, off_hours_trust: float = 90.0
    ) -> list[TimeWindow]:
        """Convenience: stricter trust required outside business hours."""
        return [
            TimeWindow("off-hours", end, start, trust_required=off_hours_trust, requires_approval=True),
        ]


class DelegationError(ValueError):
    """Raised when a delegation hop violates depth or revocation constraints."""


@dataclass
class DelegationHop:
    delegation_id: str
    source_agent: str
    target_agent: str
    capability_id: str
    depth: int
    trust_multiplier: float
    evidence_chain: tuple[str, ...]
    is_revoked: bool = False

    @property
    def can_further_delegate(self) -> bool:
        return not self.is_revoked


class DelegationChainTracker:
    """Tracks delegation chains with depth limits and per-hop trust degradation."""

    def __init__(self, *, max_depth: int = 3, trust_decay_per_hop: float = 0.85) -> None:
        self._max_depth = max_depth
        self._decay = trust_decay_per_hop
        self._chains: dict[str, list[DelegationHop]] = {}

    def delegate(
        self,
        capability_id: str,
        source_agent: str,
        target_agent: str,
        *,
        evidence_hash: str,
    ) -> DelegationHop:
        chain = self._chains.setdefault(capability_id, [])
        if chain and chain[-1].is_revoked:
            raise DelegationError("cannot delegate from a revoked chain")

        depth = len(chain) + 1
        if depth > self._max_depth:
            raise DelegationError(
                f"delegation depth {depth} exceeds max {self._max_depth}"
            )

        prev_multiplier = chain[-1].trust_multiplier if chain else 1.0
        prev_evidence = chain[-1].evidence_chain if chain else ()

        hop = DelegationHop(
            delegation_id=str(uuid.uuid4()),
            source_agent=source_agent,
            target_agent=target_agent,
            capability_id=capability_id,
            depth=depth,
            trust_multiplier=round(prev_multiplier * self._decay, 6),
            evidence_chain=(*prev_evidence, evidence_hash),
        )
        chain.append(hop)
        return hop

    def chain(self, capability_id: str) -> list[DelegationHop]:
        return list(self._chains.get(capability_id, []))

    def revoke(self, capability_id: str) -> None:
        for hop in self._chains.get(capability_id, []):
            hop.is_revoked = True

    def effective_trust(self, capability_id: str, base_trust: float) -> float:
        chain = self._chains.get(capability_id, [])
        if not chain:
            return base_trust
        return round(base_trust * chain[-1].trust_multiplier, 6)


@dataclass
class EffectivePermissions:
    agent_id: str
    capability_id: str
    can_execute: bool
    requires_approval: bool
    rate_limit: int | None
    trust_required: float
    trust_current: float
    delegation_depth: int
    evidence_hash: str


def effective_permissions(
    composition: PolicyComposition,
    *,
    trust_current: float,
    delegation_depth: int = 0,
) -> EffectivePermissions:
    """Resolve the final allow/deny decision for an agent + capability."""
    eff = composition.effective_policy
    meets_trust = trust_current >= eff.trust_required
    can_execute = composition.is_valid and eff.allow and meets_trust

    return EffectivePermissions(
        agent_id=composition.agent_id,
        capability_id=composition.capability_id,
        can_execute=can_execute,
        requires_approval=eff.requires_approval,
        rate_limit=eff.rate_limit,
        trust_required=eff.trust_required,
        trust_current=trust_current,
        delegation_depth=delegation_depth,
        evidence_hash=sha256_json(
            {
                "agent": composition.agent_id,
                "cap": composition.capability_id,
                "can_execute": can_execute,
                "trust": trust_current,
            }
        ),
    )
