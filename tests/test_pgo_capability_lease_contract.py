"""Contract probes for the first PGO -> Capability OS thin slice.

These tests exercise the public request shape before broader integration work.
They fail if the Capability OS lease/precondition fields are ignored instead of
being represented at the governed execution boundary.
"""

from cappo_backend.api.routers.exec_router import ExecRequest


def test_exec_request_preserves_capability_os_lease_contract() -> None:
    request = ExecRequest(
        prompt="perform governed write",
        workspace_id="workspace-1",
        action="repo.write",
        capability_lease={
            "mount_id": "mount-1",
            "token_id": "token-1",
            "nonce": "nonce-1",
        },
    )

    payload = request.model_dump()
    lease = payload["capability_lease"]

    assert lease is not None
    assert lease["mount_id"] == "mount-1"
    assert lease["token_id"] == "token-1"
    assert lease["nonce"] == "nonce-1"


def test_exec_request_preserves_target_precondition_contract() -> None:
    request = ExecRequest(
        prompt="perform governed write",
        workspace_id="workspace-1",
        action="repo.write",
        target_precondition={
            "target_id": "repo-1",
            "expected_state_hash": "expected",
            "observed_state_hash": "observed",
            "observed_at": "2026-08-30T12:00:00Z",
            "signature": "00",
        },
    )

    payload = request.model_dump()

    assert payload["target_precondition"] == {
        "target_id": "repo-1",
        "expected_state_hash": "expected",
        "observed_state_hash": "observed",
        "observed_at": "2026-08-30T12:00:00Z",
        "signature": "00",
    }
