import pytest
from cappo_backend.capability_mount.service import MountRegistry, MountScope
from cappo_backend.capability_mount.models import CapabilityPackage, MountPolicy
from cappo_backend.services.mount_pgl import AuditPGLAnchor

def _build_registry(db) -> MountRegistry:
    from cappo_backend.config import Settings
    settings = Settings(pgl_ledger_url="http://localhost:8001", pgl_ledger_timeout_ms=100)
    return MountRegistry(db=db, anchor=AuditPGLAnchor(db, settings=settings))

def test_arch_rule_3_model_substitution_invariance(db):
    """
    Architectural Rule 3: Model-Substitution Invariance.
    Replacing the executing model must never widen machine authority.
    The same denied capability must remain denied regardless of the attached model.
    """
    svc = _build_registry(db)
    
    pkg = CapabilityPackage(
        id="pkg_test_model_sub@v1",
        family="test",
        title="Model Sub Test",
        purpose="Prove model identity cannot widen authority",
        reads=["safe.read"],
        writes=[],
        blocked=["critical.write"]
    )
    svc.register_package(pkg)
    
    # Simulate execution by different models using the same package constraints
    models = ["qwen", "claude", "gpt-4", "local-llama-3"]
    
    for model_id in models:
        mount_record, anchor, error = svc.request_mount(
            package_ref="pkg_test_model_sub@v1",
            scope=MountScope(workspace="ws1", project="proj1"),
            role="tester",
            policy=MountPolicy(
                require_human_approval_for_external_send=False,
                require_suppression_check=False
            ),
            ttl_seconds=600,
            execution_id=f"exec_{model_id}",
            executor_spiffe_id=f"spiffe://example.org/model/{model_id}"
        )
        assert mount_record is not None, f"Failed to get mount for {model_id}"
        
        handle = mount_record.binding
        
        # 1. Allowed action should work for all
        try:
            handle.evaluate_pure("safe.read")
        except Exception:
            pytest.fail(f"Allowed action failed for {model_id}")
            
        # 2. Denied action MUST fail for all, proving no model can bypass
        with pytest.raises(Exception) as excinfo:
            handle.evaluate_pure("critical.write")
            
        assert "blocked" in str(excinfo.value).lower() or "policy" in str(excinfo.value).lower()
