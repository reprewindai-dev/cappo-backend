from datetime import datetime, timedelta, timezone

import pytest

from cappo_backend.capability_mount.engine import InMemoryAuditSink, Mounter
from cappo_backend.capability_mount.errors import (
    ExecutionTerminatedError,
    MountError,
    PolicyError,
    TokenExpiredError,
)
from cappo_backend.capability_mount.models import (
    CapabilityPackage,
    Decision,
    MountPolicy,
    MountScope,
    UnmountReason,
)


@pytest.fixture
def package() -> CapabilityPackage:
    return CapabilityPackage(
        id="outreach@v1",
        family="outreach",
        title="Governed Outreach",
        purpose="Send approved external outreach",
        reads=["contact.read"],
        writes=["draft.write", "outreach.email_send", "plain.write"],
        blocked=["credential.export"],
        outputs=["draft"],
        policy_defaults={"mode": "draft_only"},
        external_send_actions=["outreach.email_send"],
        suppression_required_actions=["outreach.email_send"],
    )


@pytest.fixture
def scope() -> MountScope:
    return MountScope(workspace="workspace-1", project="project-1")


def mount_binding(package: CapabilityPackage, scope: MountScope, **policy: object):
    mounter = Mounter()
    mount, token = mounter.mount(
        package,
        scope,
        MountPolicy(**policy) if policy else None,
    )
    sink = InMemoryAuditSink()
    from cappo_backend.capability_mount.engine import ExecutionBinding

    return mount, token, ExecutionBinding(token, sink), sink


def test_default_deny_unknown_action(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("unknown.action")
    assert sink.events[-1].decision is Decision.DENY


def test_explicit_allow_read(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.evaluate_pure("contact.read")
    assert sink.events[-1].decision is Decision.ALLOW


def test_explicit_allow_write(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, _ = mount_binding(package, scope, require_suppression_check=False)
    binding.evaluate_pure("draft.write")


def test_plain_write_does_not_require_approval(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, _ = mount_binding(package, scope)
    binding.evaluate_pure("plain.write")


def test_blocked_precedence(package: CapabilityPackage, scope: MountScope) -> None:
    package_with_overlap = package.model_copy(update={"blocked": ["draft.write"]})
    _, _, binding, _ = mount_binding(package_with_overlap, scope, require_suppression_check=False)
    with pytest.raises(PolicyError, match="blocked_action"):
        binding.evaluate_pure("draft.write")


def test_scope_mismatch_denies(package: CapabilityPackage, scope: MountScope) -> None:
    narrow_scope = scope.model_copy(update={"reads": ["contact.read"], "writes": []})
    _, _, binding, _ = mount_binding(package, narrow_scope)
    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.evaluate_pure("draft.write")


def test_ttl_expiry_denies(package: CapabilityPackage, scope: MountScope) -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    _, token = Mounter().mount(package, scope, ttl=1)
    token = token.model_copy(
        update={
            "issued_at": now,
            "expires_at": now + timedelta(seconds=1),
        }
    )
    sink = InMemoryAuditSink()
    from cappo_backend.capability_mount.engine import ExecutionBinding

    binding = ExecutionBinding(token, sink, clock=lambda: now + timedelta(seconds=2))
    with pytest.raises(TokenExpiredError):
        binding.evaluate_pure("contact.read")
    assert sink.events[-1].reason == "token_expired"


def test_explicit_terminate_denies_subsequent_calls(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.terminate()
    with pytest.raises(ExecutionTerminatedError):
        binding.evaluate_pure("contact.read")
    assert sink.events[-1].reason == "terminated"


def test_task_complete_unmount(package: CapabilityPackage, scope: MountScope) -> None:
    mount, _, binding, _ = mount_binding(package, scope)
    binding.terminate(UnmountReason.TASK_COMPLETE)
    assert mount.lifecycle.state.value == "mounted"
    with pytest.raises(ExecutionTerminatedError):
        binding.evaluate_pure("contact.read")


def test_raw_human_approval_token_never_authorizes_external_send(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, sink = mount_binding(package, scope, require_suppression_check=False)
    with pytest.raises(PolicyError, match="human_approval_not_verified"):
        binding.evaluate_pure(
            "outreach.email_send")
    assert sink.events[-1].decision is Decision.DENY
    assert sink.events[-1].reason == "human_approval_not_verified"


def test_raw_suppression_boolean_never_authorizes_suppression_gate(
    package: CapabilityPackage, scope: MountScope
) -> None:
    suppression_only = package.model_copy(update={"external_send_actions": []})
    _, _, binding, sink = mount_binding(
        suppression_only,
        scope,
        require_human_approval_for_external_send=False,
    )
    with pytest.raises(PolicyError, match="suppression_not_verified"):
        binding.evaluate_pure(
            "outreach.email_send")
    assert sink.events[-1].decision is Decision.DENY
    assert sink.events[-1].reason == "suppression_not_verified"


def test_caller_policy_cannot_weaken_package_security_defaults(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, token = Mounter().mount(
        package,
        scope,
        MountPolicy(
            mode="caller-selected-live",
            require_human_approval_for_external_send=False,
            require_suppression_check=False,
            persistent_memory_allowed=True,
        ),
    )

    assert token.policy.mode == "draft_only"
    assert token.policy.default == "deny"
    assert token.policy.require_human_approval_for_external_send is True
    assert token.policy.require_suppression_check is True
    assert token.policy.persistent_memory_allowed is False


def test_classified_actions_must_be_declared_writes(package: CapabilityPackage) -> None:
    with pytest.raises(ValueError, match="declared writes"):
        CapabilityPackage.model_validate(
            package.model_dump() | {"external_send_actions": ["not-a-write"]}
        )


def test_persistent_memory_is_package_bounded_and_ephemeral_mounts_reject_it(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, token = Mounter().mount(
        package,
        scope,
        MountPolicy(persistent_memory_allowed=True),
    )
    assert token.policy.persistent_memory_allowed is False

    unsafe_package = package.model_copy(
        update={
            "policy_defaults": {
                "mode": "draft_only",
                "persistent_memory_allowed": True,
            }
        }
    )
    with pytest.raises(MountError, match="persistent memory"):
        Mounter().mount(
            unsafe_package,
            scope,
            MountPolicy(persistent_memory_allowed=True),
        )


def test_audit_appends_on_allow_and_deny(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.evaluate_pure("contact.read")
    with pytest.raises(PolicyError):
        binding.evaluate_pure("unknown.action")
    assert [event.decision for event in sink.events] == [Decision.ALLOW, Decision.DENY]


def test_audit_hash_chain_continuity_and_tamper_detection(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.evaluate_pure("contact.read")
    with pytest.raises(PolicyError):
        binding.evaluate_pure("unknown.action")
    assert sink.verify_chain() is True
    sink.events[0] = sink.events[0].model_copy(update={"reason": "tampered"})
    assert sink.verify_chain() is False


def test_token_descriptor_contains_no_secret_material(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, token, _, _ = mount_binding(package, scope)
    descriptor = token.model_dump()
    assert "secret" not in descriptor
    assert "private_key" not in descriptor
    assert "bearer" not in descriptor
    assert "nonce" in descriptor
