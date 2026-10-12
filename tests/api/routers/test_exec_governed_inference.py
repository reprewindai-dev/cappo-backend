"""End-to-end governed inference through POST /v1/exec (non-echo path).

Mounts the built-in ``veklom.inference@v1`` package, then calls /v1/exec with the
workload identity headers a real caller sends. Consequence authority comes only
from the server-side capability lease; the provider set the executor may use comes
only from the trusted package definition.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cappo_backend.capability_mount.inference import INFERENCE_PACKAGE_ID
from cappo_backend.config import Settings
from cappo_backend.services.circuit_breaker import CircuitBreaker
from cappo_backend.services.executor import Provider, ResilientExecutor

WORKSPACE = "w1"
AUDIENCE = "https://cappo.veklom.com"


class _StubModel:
    """Stands in for a model server; records what CAPPO actually dispatched."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: list[dict[str, Any]] = []

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "response": f"answer from {self.name}",
            "model": "stub-model",
            "provider": self.name,
            "tokens": 7,
        }


@pytest.fixture
def settings() -> Settings:
    """Overrides the shared fixture: a non-echo executor and an inference package."""
    return Settings(
        database_url="sqlite:///:memory:",
        ei_signing_key="test-signing-key",
        cappo_require_persistent_pgl=False,
        cappo_internal_api_keys="test-key",
        environment="test",
        auth_enabled=False,
        api_keys="test-key",
        veklom_byos_backend_url=None,
        executor_mode="live",
        runtime_kind="amphoteric",
        runtime_instance="test-runtime",
        inference_allowed_providers="ollama",
    )


@pytest.fixture
def inference_client(
    client: TestClient, db, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    from cappo_backend.api.routers import exec_router
    from cappo_backend.models.free_run_quota import FreeRunQuota

    # Room for more than one run, so a denial below is CAPPO's, not the daily quota's.
    db.add(FreeRunQuota(workspace_id=WORKSPACE, runs_used=0, quota_limit=10))
    db.commit()

    models = {"ollama": _StubModel("ollama"), "openai": _StubModel("openai")}

    def _executor(settings, db=None, workspace_id=None, app=None):
        return ResilientExecutor(
            [
                Provider(name=name, executor=model, breaker=CircuitBreaker())
                for name, model in models.items()
            ]
        )

    monkeypatch.setattr(exec_router, "build_executor", _executor)
    client.headers.update({"X-Workspace-ID": WORKSPACE})
    client.models = models  # type: ignore[attr-defined]
    return client


def _mount(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/v1/capability/mounts",
        json={
            "package_ref": INFERENCE_PACKAGE_ID,
            "execution_scope": {"workspace": WORKSPACE, "project": "blueprint"},
            "requested_action_scope": {"writes": ["llm.exec"]},
            "role": "ephemeral_executor",
            "policy": {"mode": "draft_only"},
            "ttl_seconds": 300,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "allow", body
    return body


def _b64(value: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(value).encode()).decode()


def _sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _identity_headers(raw_body: bytes, *, execution_id: str) -> dict[str, str]:
    now = int(time.time())
    wit = {
        "iss": "https://identity.veklom.com",
        "sub": "wimse://veklom.com/w1/blueprint/studio/run",
        "aud": AUDIENCE,
        "exp": now + 120,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "cnf": {},
    }
    ect = {
        "iss": "https://identity.veklom.com",
        "sub": wit["sub"],
        "aud": AUDIENCE,
        "exp": now + 120,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "ephemeral_execution_id": execution_id,
        "candidate_act_hash": hashlib.sha256(raw_body).hexdigest(),
        "cnf": {},
    }
    wpt = {
        "htm": "POST",
        "htu": "/v1/exec",
        "body_hash": hashlib.sha256(raw_body).hexdigest(),
        "wit_hash": _sha(wit),
        "ect_hash": _sha(ect),
        "authority_hash": "",
        "jti": str(uuid.uuid4()),
        "cnf": {},
    }
    return {
        "Content-Type": "application/json",
        "Workload-Identity": _b64(wit),
        "Execution-Context": _b64(ect),
        "Workload-Proof": _b64(wpt),
    }


def _exec(
    client: TestClient,
    mount: dict[str, Any],
    *,
    action: str = "llm.exec",
    ect_execution_id: str | None = None,
    extra: dict[str, Any] | None = None,
):
    token = mount["token"]
    payload = {
        "prompt": "Summarise these notes into a plan.",
        "action": action,
        "directive": "ALLOW",
        "workspace_id": WORKSPACE,
        "capability_lease": {
            "mount_id": mount["mount"]["id"],
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "execution_id": token["execution_id"],
        },
        **(extra or {}),
    }
    raw = json.dumps(payload).encode()
    headers = _identity_headers(
        raw, execution_id=ect_execution_id or token["execution_id"]
    )
    return client.post("/v1/exec", content=raw, headers=headers)


def test_governed_inference_runs_through_the_leased_capability(inference_client):
    mount = _mount(inference_client)

    response = _exec(inference_client, mount)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response"] == "answer from ollama"
    assert body["provider"] == "ollama"
    assert body["capability_lease"]["mount_id"] == mount["mount"]["id"]
    assert body["capability_lease"]["decision"] == "allow"
    dispatched = inference_client.models["ollama"].requests
    assert len(dispatched) == 1
    assert dispatched[0]["authority_envelope"]["allowed_provider_set"] == ["ollama"]
    assert inference_client.models["openai"].requests == []


def test_caller_cannot_widen_the_provider_set(inference_client):
    mount = _mount(inference_client)

    response = _exec(
        inference_client,
        mount,
        extra={"scope": {"tools": ["llm.exec"], "allowed_provider_set": ["openai"]}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "ollama"
    assert inference_client.models["openai"].requests == []


def test_lease_is_single_use(inference_client):
    mount = _mount(inference_client)
    assert _exec(inference_client, mount).status_code == 200

    replay = _exec(inference_client, mount)

    assert replay.status_code == 403, replay.text
    assert replay.json()["detail"] == {
        "error": "CAPABILITY_LEASE_DENIED",
        "reason": "token_replay",
    }
    assert len(inference_client.models["ollama"].requests) == 1


def test_execution_context_must_name_the_leased_execution(inference_client):
    mount = _mount(inference_client)

    response = _exec(inference_client, mount, ect_execution_id=str(uuid.uuid4()))

    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "WID_EXECUTION_CONTEXT_LEASE_MISMATCH"
    assert inference_client.models["ollama"].requests == []


def test_missing_workload_identity_is_denied(inference_client):
    mount = _mount(inference_client)
    token = mount["token"]
    response = inference_client.post(
        "/v1/exec",
        json={
            "prompt": "x",
            "action": "llm.exec",
            "directive": "ALLOW",
            "capability_lease": {
                "mount_id": mount["mount"]["id"],
                "token_id": token["token_id"],
                "nonce": token["nonce"],
                "execution_id": token["execution_id"],
            },
        },
    )

    assert response.status_code == 403
    assert inference_client.models["ollama"].requests == []


def test_action_outside_the_lease_is_denied(inference_client):
    mount = _mount(inference_client)

    response = _exec(inference_client, mount, action="counter.increment")

    assert response.status_code == 403, response.text
    assert inference_client.models["ollama"].requests == []



def test_terminated_mount_cannot_run(inference_client):
    mount = _mount(inference_client)
    terminated = inference_client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/terminate",
        json={"reason": "explicit_terminate"},
    )
    assert terminated.status_code == 200, terminated.text
    assert terminated.json()["decision"] == "allow"

    response = _exec(inference_client, mount)

    assert response.status_code == 403, response.text
    assert inference_client.models["ollama"].requests == []


def test_package_with_no_allowed_providers_dispatches_nothing(inference_client):
    from cappo_backend.capability_mount.inference import inference_package

    # An operator-defined package that names no providers authorises none.
    inference_client.app.state.mount_registry.register_package(inference_package([]))
    mount = _mount(inference_client)

    response = _exec(inference_client, mount)

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"] == "PROVIDER_NOT_AUTHORIZED"
    assert inference_client.models["ollama"].requests == []
    assert inference_client.models["openai"].requests == []


def test_inference_package_is_offered_only_when_configured():
    from cappo_backend.capability_mount.inference import (
        inference_package_from_settings,
        package_allowed_providers,
    )

    assert inference_package_from_settings(Settings(inference_allowed_providers="")) is None
    package = inference_package_from_settings(
        Settings(inference_allowed_providers=" Ollama, openai ,ollama")
    )
    assert package is not None
    assert package.id == INFERENCE_PACKAGE_ID
    assert package.writes == ["llm.exec"]
    assert package_allowed_providers(package) == ["ollama", "openai"]
    assert package_allowed_providers(None) == []


def _gatekeeper_signed(raw: bytes, headers: dict[str, str], private_key) -> dict[str, str]:
    """Sign the request the way the cAPI gatekeeper does (RFC 9421, Ed25519)."""
    digest = "sha-256=:" + base64.b64encode(hashlib.sha256(raw).digest()).decode() + ":"
    covered = {
        "workload-identity": headers["Workload-Identity"],
        "execution-context": headers["Execution-Context"],
        "workload-proof": headers["Workload-Proof"],
    }
    fields = ("@method", "@target-uri", "content-digest", *covered)
    quoted = " ".join(f'"{field}"' for field in fields)
    params = f';created={int(time.time())};keyid="capi-gatekeeper"'
    base = [
        '"@method": POST',
        '"@target-uri": http://testserver/v1/exec',
        f'"content-digest": {digest}',
        *(f'"{name}": {value}' for name, value in covered.items()),
        f'"@signature-params": ({quoted}){params}',
    ]
    signature = base64.b64encode(private_key.sign("\n".join(base).encode())).decode()
    return {
        **headers,
        "Content-Digest": digest,
        "Signature-Input": f"sig1=({quoted}){params}",
        "Signature": f"sig1=:{signature}:",
    }


def test_gatekeeper_signed_request_runs_and_tampering_is_refused(
    inference_client, settings
):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from cappo_backend.api.routers.exec_router import ExecRequest
    from cappo_backend.services.canonical import canonical_json, sha256_json

    gatekeeper = Ed25519PrivateKey.generate()
    settings.capi_external_validation_enabled = True
    # cAPI/Covenant publishes its key as Base64 SPKI DER; both verifiers accept it.
    settings.capi_gatekeeper_public_key = base64.b64encode(
        gatekeeper.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()

    def signed_call(mount: dict[str, Any], *, tamper: bool = False):
        token = mount["token"]
        payload = {
            "prompt": "Summarise these notes into a plan.",
            "action": "llm.exec",
            "directive": "ALLOW",
            "pgl_id": "blueprint-studio",
            "workspace_id": WORKSPACE,
            "capability_lease": {
                "mount_id": mount["mount"]["id"],
                "token_id": token["token_id"],
                "nonce": token["nonce"],
                "execution_id": token["execution_id"],
            },
        }
        data = ExecRequest(**payload).model_dump(exclude={"security", "capability_lease"})
        nonce = str(uuid.uuid4())
        envelope = {
            "actor_id": "blueprint-studio",
            "action": "llm.exec",
            "data_hash": sha256_json(data),
            "nonce": nonce,
        }
        envelope_sig = base64.urlsafe_b64encode(
            gatekeeper.sign(canonical_json(envelope).encode())
        ).decode().rstrip("=")
        payload["security"] = {"nonce": nonce, "signature": envelope_sig}
        raw = json.dumps(payload).encode()
        headers = _gatekeeper_signed(
            raw, _identity_headers(raw, execution_id=token["execution_id"]), gatekeeper
        )
        if tamper:
            raw = raw.replace(b"Summarise", b"Exfiltrate")
            headers = {
                **headers,
                **{
                    k: v
                    for k, v in _identity_headers(
                        raw, execution_id=token["execution_id"]
                    ).items()
                    if k == "Workload-Proof"
                },
            }
        return inference_client.post("/v1/exec", content=raw, headers=headers)

    ok = signed_call(_mount(inference_client))
    assert ok.status_code == 200, ok.text
    assert ok.json()["response"] == "answer from ollama"

    tampered = signed_call(_mount(inference_client), tamper=True)
    assert tampered.status_code == 401, tampered.text
    assert tampered.json()["detail"]["error"] == "HTTP_MESSAGE_INTEGRITY_INVALID"
    assert len(inference_client.models["ollama"].requests) == 1


def test_without_a_workload_svid_the_lease_binds_to_the_principal(
    inference_client, monkeypatch
):
    """API-key or JWT callers carry no SPIFFE ID; mount and exec then both use the principal."""
    from cappo_backend.security.auth_middleware import AuthMiddleware

    original = AuthMiddleware.dispatch

    async def without_svid(self, request, call_next):
        async def strip(req):
            req.scope.pop("caller_spiffe_id", None)
            req.scope["auth_principal"] = "key:blueprint-studio"
            return await call_next(req)

        return await original(self, request, strip)

    monkeypatch.setattr(AuthMiddleware, "dispatch", without_svid)
    mount = _mount(inference_client)

    response = _exec(inference_client, mount)

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "ollama"
