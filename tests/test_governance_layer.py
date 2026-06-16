"""MCPAPI v2.0 Governance Composition Layer tests.

Covers policy composition (system/owner/runtime, deny-wins, most-restrictive
obligations), conflict detection, temporal policy adjustment, delegation chain
depth + trust decay, and effective permission resolution.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cappo_backend.services.governance import (
    DelegationChainTracker,
    DelegationError,
    Policy,
    PolicyCompositionEngine,
    PolicyRule,
    TemporalPolicyResolver,
    TimeWindow,
    effective_permissions,
)


def _allow(pid: str, **kw: object) -> Policy:
    return Policy(policy_id=pid, rules=[PolicyRule(effect="allow")], **kw)  # type: ignore[arg-type]


def _deny(pid: str, **kw: object) -> Policy:
    return Policy(policy_id=pid, rules=[PolicyRule(effect="deny")], **kw)  # type: ignore[arg-type]


def test_compose_allows_when_all_allow() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose(
        "a1",
        "search",
        system_policy=_allow("sys", trust_required=70),
        owner_policy=_allow("own", rate_limit=100),
        runtime_policy=_allow("rt", rate_limit=200),
    )
    assert comp.effective_policy.allow is True
    assert comp.effective_policy.rate_limit == 100  # most restrictive
    assert comp.effective_policy.trust_required == 70
    assert comp.effective_policy.immutable is True
    assert comp.is_valid is True


def test_compose_deny_wins() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose(
        "a1",
        "search",
        system_policy=_deny("sys"),
        owner_policy=_allow("own"),
        runtime_policy=_allow("rt"),
    )
    assert comp.effective_policy.allow is False


def test_conflict_detection_allow_deny() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose(
        "a1",
        "search",
        system_policy=_allow("sys"),
        owner_policy=_deny("own"),
    )
    kinds = {c.conflict_type for c in comp.conflicts_detected}
    assert "allow-deny" in kinds


def test_conflict_detection_rate_limit() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose(
        "a1",
        "search",
        system_policy=_allow("sys", rate_limit=10),
        owner_policy=_allow("own", rate_limit=100),
    )
    kinds = {c.conflict_type for c in comp.conflicts_detected}
    assert "rate-limit-mismatch" in kinds
    assert comp.effective_policy.rate_limit == 10


def test_temporal_off_hours_tightens_trust() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose("a1", "search", system_policy=_allow("sys", trust_required=70))
    resolver = TemporalPolicyResolver(TemporalPolicyResolver.business_hours(off_hours_trust=95))

    # 03:00 UTC is off-hours -> trust raised and approval required.
    night = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
    eff = resolver.resolve(comp.effective_policy, at=night)
    assert eff.trust_required == 95
    assert eff.requires_approval is True

    # 12:00 UTC is business hours -> unchanged.
    day = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    eff_day = resolver.resolve(comp.effective_policy, at=day)
    assert eff_day.trust_required == 70


def test_time_window_overnight_contains() -> None:
    w = TimeWindow("night", start_hour=22, end_hour=6)
    assert w.contains(23) is True
    assert w.contains(2) is True
    assert w.contains(12) is False


def test_delegation_depth_and_trust_decay() -> None:
    tracker = DelegationChainTracker(max_depth=3, trust_decay_per_hop=0.8)
    tracker.delegate("search", "root", "a", evidence_hash="h1")
    tracker.delegate("search", "a", "b", evidence_hash="h2")
    hop3 = tracker.delegate("search", "b", "c", evidence_hash="h3")
    assert hop3.depth == 3
    assert hop3.trust_multiplier == pytest.approx(0.8**3)
    assert tracker.effective_trust("search", 100.0) == pytest.approx(100.0 * 0.8**3)
    assert len(hop3.evidence_chain) == 3

    with pytest.raises(DelegationError):
        tracker.delegate("search", "c", "d", evidence_hash="h4")


def test_delegation_revoked_blocks_further() -> None:
    tracker = DelegationChainTracker(max_depth=5)
    tracker.delegate("search", "root", "a", evidence_hash="h1")
    tracker.revoke("search")
    with pytest.raises(DelegationError):
        tracker.delegate("search", "a", "b", evidence_hash="h2")


def test_effective_permissions_trust_gate() -> None:
    engine = PolicyCompositionEngine()
    comp = engine.compose("a1", "search", system_policy=_allow("sys", trust_required=80))

    granted = effective_permissions(comp, trust_current=90)
    assert granted.can_execute is True

    denied = effective_permissions(comp, trust_current=50)
    assert denied.can_execute is False
