from datetime import datetime, timedelta, timezone

import pytest

from cappo_backend.capability_mount.engine import InMemoryAuditSink, Mounter
from cappo_backend.capability_mount.errors import (
    ExecutionTerminatedError,
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
        binding.call("unknown.action", lambda: "never")
    assert sink.events[-1].decision is Decision.DENY


def test_explicit_allow_read(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    assert binding.call("contact.read", lambda: "read") == "read"
    assert sink.events[-1].decision is Decision.ALLOW


def test_explicit_allow_write(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, _ = mount_binding(package, scope, require_suppression_check=False)
    assert binding.call("draft.write", lambda: "written") == "written"


def test_plain_write_does_not_require_approval(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, _ = mount_binding(package, scope)
    assert binding.call("plain.write", lambda: "written") == "written"


def test_blocked_precedence(package: CapabilityPackage, scope: MountScope) -> None:
    package_with_overlap = package.model_copy(update={"blocked": ["draft.write"]})
    _, _, binding, _ = mount_binding(package_with_overlap, scope, require_suppression_check=False)
    with pytest.raises(PolicyError, match="blocked_action"):
        binding.call("draft.write", lambda: "never")


def test_scope_mismatch_denies(package: CapabilityPackage, scope: MountScope) -> None:
    narrow_scope = scope.model_copy(update={"reads": ["contact.read"], "writes": []})
    _, _, binding, _ = mount_binding(package, narrow_scope)
    with pytest.raises(PolicyError, match="not_in_capability_profile"):
        binding.call("draft.write", lambda: "never")


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
        binding.call("contact.read", lambda: "never")
    assert sink.events[-1].reason == "token_expired"


def test_explicit_terminate_denies_subsequent_calls(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.terminate()
    with pytest.raises(ExecutionTerminatedError):
        binding.call("contact.read", lambda: "never")
    assert sink.events[-1].reason == "terminated"


def test_task_complete_unmount(package: CapabilityPackage, scope: MountScope) -> None:
    mount, _, binding, _ = mount_binding(package, scope)
    binding.terminate(UnmountReason.TASK_COMPLETE)
    assert mount.lifecycle.state.value == "mounted"
    with pytest.raises(ExecutionTerminatedError):
        binding.call("contact.read", lambda: "never")


def test_human_approval_required_for_external_send(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, _ = mount_binding(package, scope, require_suppression_check=False)
    with pytest.raises(PolicyError, match="human_approval_required"):
        binding.call("outreach.email_send", lambda: "never", suppression_confirmed=True)
    assert (
        binding.call(
            "outreach.email_send",
            lambda: "sent",
            approval_token="approval-1",
            suppression_confirmed=True,
        )
        == "sent"
    )


def test_suppression_check_required(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, _ = mount_binding(package, scope, require_human_approval_for_external_send=False)
    with pytest.raises(PolicyError, match="suppression_check_required"):
        binding.call("outreach.email_send", lambda: "never")


def test_classified_actions_must_be_declared_writes(package: CapabilityPackage) -> None:
    with pytest.raises(ValueError, match="declared writes"):
        CapabilityPackage.model_validate(
            package.model_dump() | {"external_send_actions": ["not-a-write"]}
        )


def test_persistent_memory_disallowed_by_default(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, token, _, _ = mount_binding(package, scope)
    assert token.policy.persistent_memory_allowed is False
    with pytest.raises(Exception, match="persistent memory"):
        Mounter().mount(package, scope, MountPolicy(persistent_memory_allowed=True))


def test_audit_appends_on_allow_and_deny(package: CapabilityPackage, scope: MountScope) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.call("contact.read", lambda: "read")
    with pytest.raises(PolicyError):
        binding.call("unknown.action", lambda: "never")
    assert [event.decision for event in sink.events] == [Decision.ALLOW, Decision.DENY]


def test_audit_hash_chain_continuity_and_tamper_detection(
    package: CapabilityPackage, scope: MountScope
) -> None:
    _, _, binding, sink = mount_binding(package, scope)
    binding.call("contact.read", lambda: "read")
    with pytest.raises(PolicyError):
        binding.call("unknown.action", lambda: "never")
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
