import pytest
from sqlalchemy.orm import Session
from cappo_backend.capability_mount.service import MountRegistry, LocalConfirmedAnchor
from cappo_backend.capability_mount.models import (
    CapabilityPackage, MountScope, MountPolicy, Decision
)

def _build_registry(db: Session) -> MountRegistry:
    reg = MountRegistry(db=db, anchor=LocalConfirmedAnchor())
    
    # Provider A - cheap, authorized
    reg.register_package(CapabilityPackage(
        id="provider-a@v1", family="storage", title="A", purpose="test",
        reads=["blob.read"], writes=[], policy_defaults={"cost": 10}
    ))
    
    # Provider B - expensive
    reg.register_package(CapabilityPackage(
        id="provider-b@v1", family="storage", title="B", purpose="test",
        reads=["blob.read"], writes=[], policy_defaults={"cost": 1000}
    ))
    
    # Provider C - forbidden
    reg.register_package(CapabilityPackage(
        id="provider-c@v1", family="storage", title="C", purpose="test",
        reads=["blob.read"], writes=[], policy_defaults={"cost": 10}
    ))
    
    return reg

def test_resolve_authorized_only(db: Session):
    """Case 1: Three technically compatible, only one authorized."""
    reg = _build_registry(db)
    candidates = ["provider-a@v1", "provider-b@v1", "provider-c@v1"]
    
    context = {"budget": 100, "forbidden_providers": ["provider-c@v1"]}
    eval_result = reg.evaluate_candidates(candidates, context)
    
    assert eval_result.decision == Decision.ALLOW
    assert eval_result.permitted_candidates == ["provider-a@v1"]
    assert eval_result.rejected_candidates["provider-b@v1"] == "budget_ceiling"
    assert eval_result.rejected_candidates["provider-c@v1"] == "policy_forbidden"
    
    record, _, reason = reg.request_mount(
        package_ref="provider-a@v1",
        scope=MountScope(workspace="ws1", project="p1"),
        role="test", policy=MountPolicy(), ttl_seconds=300,
        owner_principal="spiffe://cappo/test",
        authority_evaluation=eval_result
    )
    assert record is not None, f"Mount failed: {reason}"

def test_no_authorized_provider(db: Session):
    """Case 2: No authorized provider -> fail closed."""
    reg = _build_registry(db)
    candidates = ["provider-b@v1", "provider-c@v1"]
    
    context = {"budget": 100, "forbidden_providers": ["provider-c@v1"]}
    eval_result = reg.evaluate_candidates(candidates, context)
    
    assert eval_result.decision == Decision.DENY
    assert eval_result.permitted_candidates == []
    
    record, _, reason = reg.request_mount(
        package_ref="provider-b@v1",
        scope=MountScope(workspace="ws1", project="p1"),
        role="test", policy=MountPolicy(), ttl_seconds=300,
        owner_principal="spiffe://cappo/test",
        authority_evaluation=eval_result
    )
    assert record is None
    assert reason == "authority_denied"

def test_budget_changes_before_binding(db: Session):
    """Case 3: Budget changes after discovery but before binding."""
    reg = _build_registry(db)
    candidates = ["provider-a@v1"]
    
    # Discovery stage initially evaluated ok
    context = {"budget": 100}
    eval_result = reg.evaluate_candidates(candidates, context)
    assert eval_result.decision == Decision.ALLOW
    
    # Before binding, budget changes -> Re-evaluate candidate
    context_new = {"budget": 5}
    new_eval_result = reg.evaluate_candidates(candidates, context_new)
    
    assert new_eval_result.decision == Decision.DENY
    assert new_eval_result.rejected_candidates["provider-a@v1"] == "budget_ceiling"
    
    record, _, reason = reg.request_mount(
        package_ref="provider-a@v1",
        scope=MountScope(workspace="ws1", project="p1"),
        role="test", policy=MountPolicy(), ttl_seconds=300,
        owner_principal="spiffe://cappo/test",
        authority_evaluation=new_eval_result
    )
    assert record is None
    assert reason == "authority_denied"

def test_resolved_provider_substituted(db: Session):
    """Case 4: Resolved provider substituted after authorization -> DENY."""
    reg = _build_registry(db)
    candidates = ["provider-a@v1", "provider-b@v1"]
    
    # Both pass initial technical check, but eval restricts to A due to budget
    context = {"budget": 100}
    eval_result = reg.evaluate_candidates(candidates, context)
    assert eval_result.permitted_candidates == ["provider-a@v1"]
    
    record, _, reason = reg.request_mount(
        package_ref="provider-b@v1",
        scope=MountScope(workspace="ws1", project="p1"),
        role="test", policy=MountPolicy(), ttl_seconds=300,
        owner_principal="spiffe://cappo/test",
        authority_evaluation=eval_result
    )
    assert record is None
    assert reason == "binding_mismatch"

def test_provider_disappears(db: Session):
    """Case 5: Provider disappears after authorization -> unavailable."""
    reg = _build_registry(db)
    candidates = ["provider-a@v1"]
    
    context = {"budget": 100}
    eval_result = reg.evaluate_candidates(candidates, context)
    assert eval_result.decision == Decision.ALLOW
    
    # Provider disappears from registry
    del reg.packages["provider-a@v1"]
    
    record, _, reason = reg.request_mount(
        package_ref="provider-a@v1",
        scope=MountScope(workspace="ws1", project="p1"),
        role="test", policy=MountPolicy(), ttl_seconds=300,
        owner_principal="spiffe://cappo/test",
        authority_evaluation=eval_result
    )
    assert record is None
    # Cannot transfer authority, just returns provider disappeared
    assert reason == "provider_disappeared"
