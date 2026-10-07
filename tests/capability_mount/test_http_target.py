"""HttpTargetAdapter: honest outcome mapping for an external target's /apply."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cappo_backend.capability_mount.effects import CappoUncertainError, ConsequenceContext
from cappo_backend.capability_mount.errors import TargetRefusedError
from cappo_backend.capability_mount.http_target import HttpTargetAdapter, load_http_targets


def serve(status: int, answer: dict | None = None, seen: list | None = None):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if seen is not None:
                seen.append((body, self.headers.get("X-Veklom-Permit")))
            raw = json.dumps(answer or {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/apply"


def ctx(**over) -> ConsequenceContext:
    base = dict(action="record.modify", resource="42", arguments={"note": "approved-by-veklom"},
                operation_id="op-1", permit_for=lambda body: "permit-for-" + str(len(body)))
    base.update(over)
    return ConsequenceContext(**base)


def test_sends_exact_body_with_a_permit_for_those_bytes_and_returns_applied():
    seen: list = []
    server, url = serve(201, {"decision": "applied", "reason": "redeemed", "receipt": {"seq": 7}}, seen)
    try:
        result = HttpTargetAdapter("arena.protected-dataset", url).dispatch(ctx())
    finally:
        server.shutdown()
    body, permit = seen[0]
    assert json.loads(body) == {"operation_id": "op-1", "op": "modify", "record_id": 42,
                                "fields": {"note": "approved-by-veklom"}}
    assert permit == f"permit-for-{len(body)}"  # minted for the exact bytes sent
    assert result["decision"] == "applied"


@pytest.mark.parametrize("status", [400, 403, 409])
def test_target_refusal_is_a_definite_no(status):
    server, url = serve(status, {"decision": "refused", "reason": "cappo_denied:mount_terminated"})
    try:
        with pytest.raises(TargetRefusedError, match="target_refused"):
            HttpTargetAdapter("t", url).dispatch(ctx())
    finally:
        server.shutdown()


@pytest.mark.parametrize("status", [500, 503])
def test_server_trouble_is_uncertain_never_assumed(status):
    server, url = serve(status, {})
    try:
        with pytest.raises(CappoUncertainError):
            HttpTargetAdapter("t", url).dispatch(ctx())
    finally:
        server.shutdown()


def test_refuses_without_permits_or_operation_id_and_sends_nothing():
    seen: list = []
    server, url = serve(201, {"decision": "applied"}, seen)
    try:
        with pytest.raises(TargetRefusedError, match="permits_not_configured"):
            HttpTargetAdapter("t", url).dispatch(ctx(permit_for=None))
        with pytest.raises(TargetRefusedError, match="target_requires_operation_id"):
            HttpTargetAdapter("t", url).dispatch(ctx(operation_id=None))
    finally:
        server.shutdown()
    assert seen == []


def test_config_loader_carries_the_pinned_key():
    [adapter] = load_http_targets(json.dumps([{"ref": "arena.protected-dataset", "apply_url": "http://x/apply",
                                                "redeem_public_key_hex": "ab" * 32, "actions": ["record.modify"]}]))
    assert adapter.ref == "arena.protected-dataset"
    assert adapter.redeem_public_key_hex == "ab" * 32
    assert adapter.actions == frozenset({"record.modify"})
