from __future__ import annotations

from cappo_backend.services.orchestrator import _execution_request


def test_execution_request_uses_signed_identity_scope_not_client_provider_set() -> None:
    request = {
        "prompt": "hello",
        "authority_envelope": {"allowed_provider_set": ["attacker-provider"]},
    }
    identity = {
        "execution_id": "exec-1",
        "runtime_ownership": {"authority_epoch": 9},
        "scope": {"allowed_provider_set": ["provider-a", "provider-b"]},
    }

    execution_request = _execution_request(request, identity)

    assert execution_request["authority_envelope"] == {
        "execution_id": "exec-1",
        "authority_epoch": 9,
        "allowed_provider_set": ["provider-a", "provider-b"],
    }
