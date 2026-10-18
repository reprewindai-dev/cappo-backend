"""No authority, no compute: an atomic, CAPPO-recorded right to start.

A package with ``requires_start_claim`` (e.g. a compute job whose result is committed later)
must claim its start before the work runs. The claim locks the mount row exactly as terminate()
does, so a revocation lands either before the claim (no start) or after it (the commit is still
refused). A revoked result is never let through.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from cappo_backend.capability_mount.effects import LocalRecordAdapter, TargetAdapterRegistry
from tests.capability_mount.test_terminate_fence import (
    ConfirmedAnchor,
    execute_payload,
    owner_terminate,
    records_package,
)


def _setup(client: TestClient, root: Path, *, requires_claim: bool = True) -> LocalRecordAdapter:
    registry = client.app.state.mount_registry
    registry.register_package(records_package().model_copy(update={"requires_start_claim": requires_claim}))
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    adapter = LocalRecordAdapter(root / "records")
    registry.target_adapters.register(LocalRecordAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "w1"
    return adapter


def _mount(client: TestClient) -> dict:
    body = client.post("/v1/capability/mounts", json={
        "package_ref": "records@v1",
        "execution_scope": {"workspace": "w1", "project": "p1"},
        "requested_action_scope": {"reads": [], "writes": ["record.create"]},
        "ttl_seconds": 300,
    }).json()
    assert body["decision"] == "allow"
    return body


def _claim(client: TestClient, body: dict, operation_id: str, **override: str) -> dict:
    token = body["token"]
    payload = {"token_id": token["token_id"], "nonce": token["nonce"], "operation_id": operation_id, **override}
    response = client.post(f"/v1/capability/mounts/{body['mount']['id']}/start-claim", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _commit(client: TestClient, body: dict, operation_id: str) -> dict:
    return client.post(f"/v1/capability/mounts/{body['mount']['id']}/execute",
                       json=execute_payload(body, operation_id)).json()


def test_a_normal_authorized_job_claims_its_start_runs_and_commits_exactly_once(
    client: TestClient, tmp_path: Path
) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)

    claim = _claim(client, body, "job-1")
    assert (claim["decision"], claim["reason"]) == ("allow", "start_claimed")

    first = _commit(client, body, "job-1")
    assert first["decision"] == "allow" and first["consequence"]["state"] == "succeeded"
    assert adapter.invocation_count == 1

    again = _commit(client, body, "job-1")
    assert again["decision"] == "deny"
    assert adapter.invocation_count == 1  # committed exactly once


def test_revoked_before_the_claim_means_the_work_never_starts(
    client: TestClient, tmp_path: Path, db: Session
) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)

    owner_terminate(db, body["mount"]["id"])

    claim = _claim(client, body, "job-1")
    assert (claim["decision"], claim["reason"]) == ("deny", "terminated")
    assert adapter.invocation_count == 0


def test_revoked_after_the_claim_the_result_is_still_refused(
    client: TestClient, tmp_path: Path, db: Session
) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)
    assert _claim(client, body, "job-1")["decision"] == "allow"

    owner_terminate(db, body["mount"]["id"])  # revoked while the work runs

    result = _commit(client, body, "job-1")
    assert result["decision"] == "deny"  # never let a revoked result through
    assert adapter.invocation_count == 0


def test_one_claim_per_mount_and_a_retry_of_the_same_claim_is_idempotent(client: TestClient, tmp_path: Path) -> None:
    _setup(client, tmp_path)
    body = _mount(client)
    assert _claim(client, body, "job-1")["decision"] == "allow"

    # The first response was lost; the same operation asks again and learns its claim stands.
    replay = _claim(client, body, "job-1")
    assert (replay["decision"], replay["reason"]) == ("allow", "start_claim_replayed")
    # Any other operation is refused: one claim per mount.
    other = _claim(client, body, "job-2")
    assert (other["decision"], other["reason"]) == ("deny", "start_already_claimed")


def test_a_retry_after_revocation_is_refused_not_replayed(
    client: TestClient, tmp_path: Path, db: Session
) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)
    assert _claim(client, body, "job-1")["decision"] == "allow"  # recorded; response "lost"

    owner_terminate(db, body["mount"]["id"])  # revoked during the worker's recovery

    retry = _claim(client, body, "job-1")
    assert (retry["decision"], retry["reason"]) == ("deny", "terminated")
    assert _commit(client, body, "job-1")["decision"] == "deny"
    assert adapter.invocation_count == 0


def test_a_lost_claim_response_then_retry_still_commits_exactly_once(client: TestClient, tmp_path: Path) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)
    _claim(client, body, "job-1")  # response lost
    assert _claim(client, body, "job-1")["reason"] == "start_claim_replayed"  # retry

    assert _commit(client, body, "job-1")["decision"] == "allow"
    assert _commit(client, body, "job-1")["decision"] == "deny"
    assert adapter.invocation_count == 1


def test_commit_without_a_start_claim_is_refused(client: TestClient, tmp_path: Path) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)

    result = _commit(client, body, "job-1")

    assert (result["decision"], result["reason"]) == ("deny", "start_not_claimed")
    assert adapter.invocation_count == 0


def test_commit_for_a_different_operation_than_the_claimed_one_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    adapter = _setup(client, tmp_path)
    body = _mount(client)
    assert _claim(client, body, "job-1")["decision"] == "allow"

    result = _commit(client, body, "job-other")

    assert (result["decision"], result["reason"]) == ("deny", "start_not_claimed")
    assert adapter.invocation_count == 0


def test_a_claim_needs_the_mount_s_own_token(client: TestClient, tmp_path: Path) -> None:
    _setup(client, tmp_path)
    body = _mount(client)

    claim = _claim(client, body, "job-1", nonce="not-the-nonce")

    assert (claim["decision"], claim["reason"]) == ("deny", "token_mismatch")


def test_packages_without_the_requirement_are_unchanged(client: TestClient, tmp_path: Path) -> None:
    adapter = _setup(client, tmp_path, requires_claim=False)
    body = _mount(client)

    result = _commit(client, body, "op-1")

    assert result["decision"] == "allow" and adapter.invocation_count == 1
