"""Private Cloud governed compute: compute.job@v1 commits to the results sink, once.

The worker presents the job's single-use holder credential: it must claim its start first,
then its one execute dispatches the result to the independent sink and ends the mount.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from cappo_backend.capability_mount.effects import TargetAdapterRegistry
from cappo_backend.capability_mount.fabric_compute import FABRIC_COMPUTE_JOB_PACKAGE, ResultSinkAdapter
from cappo_backend.config import Settings
from tests.capability_mount.test_terminate_fence import ConfirmedAnchor


class _Sink:
    """A local results sink that records what it receives and checks the bearer."""

    def __init__(self, token: str) -> None:
        self.received: list[tuple[str, dict]] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                sink.received.append((self.headers.get("Authorization", ""), body))
                ok = self.headers.get("Authorization") == f"Bearer {token}"
                self.send_response(201 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"stored": ok}).encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/effect"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def _setup(client: TestClient, sink: _Sink, token: str) -> ResultSinkAdapter:
    registry = client.app.state.mount_registry
    registry.register_package(FABRIC_COMPUTE_JOB_PACKAGE)
    registry.anchor = ConfirmedAnchor()
    registry.target_adapters = TargetAdapterRegistry()
    adapter = ResultSinkAdapter(sink.url, token)
    registry.target_adapters.register(ResultSinkAdapter.ref, adapter)
    client.headers["X-Workspace-ID"] = "ws-customer"
    return adapter


def _mount(client: TestClient, job_id: str) -> dict:
    body = client.post("/v1/capability/mounts", json={
        "package_ref": "compute.job@v1",
        "execution_scope": {"workspace": "ws-customer", "project": "private-cloud"},
        "requested_action_scope": {"reads": [], "writes": ["job.commit"]},
        "ttl_seconds": 900,
        "execution_id": job_id,
    }).json()
    assert body["decision"] == "allow", body
    return body


def _execute(client: TestClient, body: dict, job_id: str) -> dict:
    token = body["token"]
    return client.post(f"/v1/capability/mounts/{body['mount']['id']}/execute", json={
        "token_id": token["token_id"], "nonce": token["nonce"], "action": "job.commit",
        "target_ref": ResultSinkAdapter.ref, "resource": job_id, "operation_id": job_id,
        "arguments": {"job_id": job_id, "result_hash": "ab" * 32, "worker_id": "wkr_1",
                      "hostname": "box", "elapsed_s": 1.0, "output": {"n": 1}},
    }).json()


def test_a_job_commits_to_the_sink_exactly_once_and_only_after_claiming_its_start(client: TestClient) -> None:
    sink = _Sink("sink-token")
    adapter = _setup(client, sink, "sink-token")
    body = _mount(client, "job-a")

    unclaimed = _execute(client, body, "job-a")
    assert unclaimed["decision"] == "deny"          # no claim, no commit
    assert sink.received == []

    token = body["token"]
    claim = client.post(f"/v1/capability/mounts/{body['mount']['id']}/start-claim",
                        json={"token_id": token["token_id"], "nonce": token["nonce"], "operation_id": "job-a"}).json()
    assert claim["decision"] == "allow"

    committed = _execute(client, body, "job-a")
    assert committed["decision"] == "allow" and committed["consequence"]["state"] == "succeeded"
    assert adapter.invocation_count == 1
    assert sink.received == [("Bearer sink-token", {
        "job_id": "job-a", "op_id": "job-a", "worker_id": "wkr_1", "hostname": "box",
        "result_hash": "ab" * 32, "elapsed_s": 1.0, "output": {"n": 1}})]

    again = _execute(client, body, "job-a")
    assert again["decision"] == "deny"               # single use: the mount ended with the commit
    assert len(sink.received) == 1


def test_a_sink_that_refuses_commits_nothing(client: TestClient) -> None:
    sink = _Sink("right-token")
    _setup(client, sink, "wrong-token")
    body = _mount(client, "job-b")
    token = body["token"]
    client.post(f"/v1/capability/mounts/{body['mount']['id']}/start-claim",
                json={"token_id": token["token_id"], "nonce": token["nonce"], "operation_id": "job-b"})
    result = _execute(client, body, "job-b")
    assert result["decision"] != "allow" or result["consequence"]["state"] != "succeeded"


def test_the_package_is_offered_only_when_a_sink_is_configured() -> None:
    from cappo_backend.main import create_app

    base = dict(environment="development", auth_enabled=False)
    with_sink = create_app(Settings(**base, fabric_result_sink_url="http://sink/effect", fabric_result_sink_token="t"))
    without = create_app(Settings(**base))
    assert "compute.job@v1" in with_sink.state.mount_registry.packages
    assert with_sink.state.mount_registry.target_adapters.resolve(ResultSinkAdapter.ref) is not None
    assert "compute.job@v1" not in without.state.mount_registry.packages
