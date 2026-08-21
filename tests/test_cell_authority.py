from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cappo_backend.config import Settings
from cappo_backend.services.canonical import verify_signature_ed25519
from cappo_backend.services.cell_authority import (
    CellAuthorityBuilder,
    CellAuthorityError,
    GitHubFileUpdateIntent,
    cell_authority_public_key_b64url,
    semantic_intent_digest,
)


IMAGE_DIGEST = "sha256:" + "b" * 64
KERNEL_DIGEST = "sha256:" + "c" * 64


def _effect(**updates):
    value = {
        "provider": "github",
        "operation": "github.file.update",
        "owner": "reprewindai-dev",
        "repo": "sandbox",
        "branch": "main",
        "path": "README.md",
        "expected_blob_sha": "a" * 40,
        "content_b64": base64.b64encode(b"governed\n").decode("ascii"),
        "commit_message": "test: governed update",
    }
    value.update(updates)
    return value


def _identity(*, providers=None, expires_at=None):
    return {
        "execution_id": "exec-1",
        "subject": "agent-1",
        "scope": {"tools": ["github.file.update"], "allowed_provider_set": providers or ["github"]},
        "policy_hash": "sha256:policy",
        "expires_at": (expires_at or (datetime.now(timezone.utc) + timedelta(minutes=5))).isoformat(),
        "runtime_ownership": {
            "path_id": "exec-1",
            "assignment_id": "assignment-1",
            "authority_epoch": 7,
            "runtime_kind": "cappo",
            "runtime_instance": "cappo-1",
        },
    }


def _request(**updates):
    value = {
        "workspace_id": "workspace-1",
        "action": "github.file.update",
        "effect": _effect(),
        "cell_limits": {"cpus": 0.25, "memory_mb": 96, "pids": 24, "timeout_seconds": 15, "tmpfs_mb": 32},
        "budget_approved_cents": 200,
        "request_id": "request-1",
        "idempotency_key": "idem-1",
        "authority_envelope": {
            "execution_id": "exec-1",
            "authority_epoch": 7,
            "allowed_provider_set": ["github"],
        },
    }
    value.update(updates)
    return value


class _FakeDB:
    def __init__(self, identity):
        self.record = SimpleNamespace(identity_json=identity, tenant_id="workspace-1")

    def get(self, model, key):
        assert key == "exec-1"
        return self.record


def _builder(monkeypatch, ttl="30", *, isolation="os-enforced"):
    monkeypatch.setenv("CAPPO_CELL_AUTHORITY_KID", "cappo-cell-v1")
    monkeypatch.setenv("CAPPO_LOCKERPHYCER_CELL_INSTANCE", "lockerphycer-host-a")
    monkeypatch.setenv("CAPPO_CELL_AUTHORITY_TTL_SECONDS", ttl)
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_IMAGE", f"lockerphycer-executor@{IMAGE_DIGEST}")
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_ISOLATION", isolation)
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_NETWORK_POLICY_DIGEST", "network:none")
    if isolation == "microvm":
        monkeypatch.setenv("CAPPO_GOVERNED_CELL_KERNEL_SHA256", KERNEL_DIGEST)
    else:
        monkeypatch.delenv("CAPPO_GOVERNED_CELL_KERNEL_SHA256", raising=False)
    settings = Settings(ei_signing_key="unit-test-cell-authority-key")
    return CellAuthorityBuilder(settings), settings


def test_persisted_execution_identity_mints_exact_signed_cell_authority(monkeypatch):
    builder, settings = _builder(monkeypatch)
    request = _request()

    signed = builder.build_from_execution_request(request, _FakeDB(_identity()))
    envelope = signed["envelope"]

    assert envelope["execution_id"] == "exec-1"
    assert envelope["path_id"] == "exec-1"
    assert envelope["assignment_id"] == "assignment-1"
    assert envelope["authority_epoch"] == 7
    assert envelope["runtime_kind"] == "lockerphycer-cell"
    assert envelope["runtime_instance"] == "lockerphycer-host-a"
    assert envelope["required_isolation"] == "os-enforced"
    assert envelope["runtime_image_digest"] == IMAGE_DIGEST
    assert envelope["runtime_kernel_digest"] is None
    assert envelope["network_policy_digest"] == "network:none"
    assert envelope["workspace_id"] == "workspace-1"
    assert envelope["tenant_id"] == "workspace-1"
    assert envelope["allowed_provider_set"] == ["github"]
    assert envelope["capability_id"] == "github.file.update"
    assert envelope["semantic_intent_digest"] == semantic_intent_digest(
        GitHubFileUpdateIntent.model_validate(request["effect"])
    )
    assert envelope["resource_constraints"]["memory_mb"] == 96

    public_raw = base64.urlsafe_b64decode(
        cell_authority_public_key_b64url(settings.ei_signing_key) + "=="
    )
    assert verify_signature_ed25519(
        envelope,
        signed["proof"]["signature_b64url"],
        public_raw,
    ) is True


def test_microvm_authority_binds_kernel_and_rootfs_measurements(monkeypatch):
    builder, settings = _builder(monkeypatch, isolation="microvm")
    signed = builder.build_from_execution_request(_request(), _FakeDB(_identity()))
    envelope = signed["envelope"]

    assert envelope["required_isolation"] == "microvm"
    assert envelope["runtime_image_digest"] == IMAGE_DIGEST
    assert envelope["runtime_kernel_digest"] == KERNEL_DIGEST
    assert envelope["network_policy_digest"] == "network:none"

    public_raw = base64.urlsafe_b64decode(
        cell_authority_public_key_b64url(settings.ei_signing_key) + "=="
    )
    assert verify_signature_ed25519(envelope, signed["proof"]["signature_b64url"], public_raw)


def test_microvm_authority_fails_closed_without_kernel_measurement(monkeypatch):
    monkeypatch.setenv("CAPPO_CELL_AUTHORITY_KID", "cappo-cell-v1")
    monkeypatch.setenv("CAPPO_LOCKERPHYCER_CELL_INSTANCE", "lockerphycer-host-a")
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_IMAGE", f"lockerphycer-rootfs@{IMAGE_DIGEST}")
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_ISOLATION", "microvm")
    monkeypatch.delenv("CAPPO_GOVERNED_CELL_KERNEL_SHA256", raising=False)

    with pytest.raises(CellAuthorityError, match="KERNEL_SHA256"):
        CellAuthorityBuilder(Settings(ei_signing_key="unit-test-cell-authority-key"))


def test_cell_authority_never_outlives_parent_execution_identity(monkeypatch):
    builder, _ = _builder(monkeypatch, ttl="300")
    parent_expiry = datetime.now(timezone.utc) + timedelta(seconds=8)

    signed = builder.build_from_execution_request(
        _request(),
        _FakeDB(_identity(expires_at=parent_expiry)),
    )

    child_expiry = datetime.fromisoformat(signed["envelope"]["expires_at"])
    assert child_expiry <= parent_expiry


def test_provider_not_in_persisted_identity_is_denied(monkeypatch):
    builder, _ = _builder(monkeypatch)

    with pytest.raises(CellAuthorityError, match="does not authorize provider github"):
        builder.build_from_execution_request(
            _request(),
            _FakeDB(_identity(providers=["ollama"])),
        )


def test_workspace_hint_cannot_override_persisted_identity(monkeypatch):
    builder, _ = _builder(monkeypatch)

    with pytest.raises(CellAuthorityError, match="workspace does not match"):
        builder.build_from_execution_request(
            _request(workspace_id="other-workspace"),
            _FakeDB(_identity()),
        )


def test_repository_path_escape_is_rejected_before_authority(monkeypatch):
    builder, _ = _builder(monkeypatch)
    request = _request(effect=_effect(path="../secret"))

    with pytest.raises(Exception, match="parent traversal"):
        builder.build_from_execution_request(request, _FakeDB(_identity()))
