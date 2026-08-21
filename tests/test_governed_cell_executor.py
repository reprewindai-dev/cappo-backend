from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from cappo_backend.config import Settings
from cappo_backend.services.governed_cell_executor import (
    GovernedCellDispatchExecutor,
    GovernedCellExecutionError,
    LockerphycerCellExecutor,
)


def _effect():
    return {
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


def _identity():
    return {
        "execution_id": "exec-1",
        "subject": "agent-1",
        "scope": {"tools": ["github.file.update"], "allowed_provider_set": ["github"]},
        "policy_hash": "sha256:policy",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        "runtime_ownership": {
            "path_id": "exec-1",
            "assignment_id": "assignment-1",
            "authority_epoch": 1,
            "runtime_kind": "cappo",
            "runtime_instance": "cappo-1",
        },
    }


def _request(action="github.file.update"):
    return {
        "workspace_id": "workspace-1",
        "action": action,
        "effect": _effect(),
        "budget_approved_cents": 100,
        "authority_envelope": {
            "execution_id": "exec-1",
            "authority_epoch": 1,
            "allowed_provider_set": ["github"],
        },
    }


class _FakeDB:
    def get(self, model, key):
        return SimpleNamespace(identity_json=_identity(), tenant_id="workspace-1")


class _Inner:
    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return {"response": "inner", "provider": "inner", "tokens": 1}


class _Cell:
    def __init__(self, error=None):
        self.calls = 0
        self.authority = None
        self.error = error

    def execute(self, request, authority):
        self.calls += 1
        self.authority = authority
        if self.error:
            raise self.error
        return {"response": "cell", "provider": "lockerphycer-governed-cell", "tokens": 0}


def _dispatch(monkeypatch, *, inner=None, cell=None):
    monkeypatch.setenv("CAPPO_CELL_AUTHORITY_KID", "cell-v1")
    monkeypatch.setenv("CAPPO_LOCKERPHYCER_CELL_INSTANCE", "lockerphycer-host-a")
    settings = Settings(ei_signing_key="unit-test-cell-authority-key")
    return GovernedCellDispatchExecutor(
        inner=inner or _Inner(),
        settings=settings,
        db=_FakeDB(),
        cell=cell or _Cell(),
    )


def test_github_consequence_goes_only_to_governed_cell(monkeypatch):
    inner = _Inner()
    cell = _Cell()
    dispatch = _dispatch(monkeypatch, inner=inner, cell=cell)

    result = dispatch.execute(_request())

    assert result["provider"] == "lockerphycer-governed-cell"
    assert cell.calls == 1
    assert inner.calls == 0
    assert cell.authority["envelope"]["runtime_instance"] == "lockerphycer-host-a"
    assert cell.authority["envelope"]["capability_id"] == "github.file.update"


def test_governed_cell_failure_never_falls_back_to_inner(monkeypatch):
    inner = _Inner()
    cell = _Cell(error=GovernedCellExecutionError("cell denied"))
    dispatch = _dispatch(monkeypatch, inner=inner, cell=cell)

    with pytest.raises(GovernedCellExecutionError, match="cell denied"):
        dispatch.execute(_request())

    assert cell.calls == 1
    assert inner.calls == 0


def test_non_cell_action_preserves_existing_executor(monkeypatch):
    inner = _Inner()
    cell = _Cell()
    dispatch = _dispatch(monkeypatch, inner=inner, cell=cell)

    result = dispatch.execute(_request(action="llm.exec"))

    assert result["provider"] == "inner"
    assert inner.calls == 1
    assert cell.calls == 0


def test_uds_client_sends_no_provider_credential_to_untrusted_cell(monkeypatch):
    host_key = "h" * 40
    monkeypatch.setenv("CAPPO_LOCKERPHYCER_CELL_HOST_API_KEY", host_key)
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_IMAGE", "ghcr.io/veklom/cell-executor@sha256:" + "b" * 64)
    monkeypatch.setenv("CAPPO_GOVERNED_CELL_COMMAND_JSON", '["python","/app/main.py"]')

    effect = _effect()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append((request.url.path, body, dict(request.headers)))
        assert request.headers["X-Cell-Host-Key"] == host_key
        if request.url.path == "/v1/cells/run":
            serialized = json.dumps(effect, sort_keys=True, separators=(",", ":"))
            return httpx.Response(
                200,
                json={
                    "cell_id": "cell-1",
                    "execution_id": "exec-1",
                    "grant_id": "grant-1",
                    "started_at": "2026-08-21T10:00:00Z",
                    "completed_at": "2026-08-21T10:00:01Z",
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout": serialized,
                    "stderr": "",
                    "runtime": "podman",
                    "network_mode": "none",
                    "credential_mode": "brokered_only",
                    "teardown_confirmed": True,
                    "authority_digest": "sha256:authority",
                },
            )
        if request.url.path == "/v1/effects/github/file-update":
            return httpx.Response(
                200,
                json={
                    "provider": "github",
                    "operation": "github.file.update",
                    "repository": "reprewindai-dev/sandbox",
                    "branch": "main",
                    "path": "README.md",
                    "before_sha": "a" * 40,
                    "after_blob_sha": "b" * 40,
                    "commit_sha": "c" * 40,
                    "effect_digest": "sha256:effect",
                    "mutation_succeeded": True,
                    "credential_revoked": True,
                    "security_status": "COMPLETE",
                },
            )
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://lockerphycer-cell-host")
    executor = LockerphycerCellExecutor(client=client)
    authority = {
        "envelope": {"execution_id": "exec-1"},
        "proof": {"algorithm": "Ed25519", "key_id": "kid", "signature_b64url": "x" * 64},
    }

    result = executor.execute({"effect": effect}, authority)

    assert result["credential_revocation_confirmed"] is True
    assert result["governed_cell"]["network_mode"] == "none"
    cell_payload = seen[0][1]
    assert cell_payload["safe_environment"] == {}
    serialized_cell_payload = json.dumps(cell_payload).upper()
    assert "GITHUB_TOKEN" not in serialized_cell_payload
    assert "PRIVATE_KEY" not in serialized_cell_payload
    assert len(seen) == 2
