"""Execute and capture the Activation v1 runtime proof matrix."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1"))
REQ = ROOT / "requests"
RESP = ROOT / "responses"
LOGS = ROOT / "logs"
DB = ROOT / "db" / "cappo.db"
BASE = os.environ.get("ACTIVATION_BASE_URL", "https://127.0.0.1:8443")
PYTHON = "/home/ubuntu/repos/cappo-backend/.venv/bin/python"
RUNTIME = ROOT / "runtime_app.py"
CERT = ROOT / "mtls" / "client.crt"
KEY = ROOT / "mtls" / "client.key"
CA = ROOT / "mtls" / "ca.crt"
WORKSPACE = "workspace-activation-v1"
PROJECT = "activation-v1"
PACKAGE = "activation@v1"
SPIFFE = "spiffe://example.org/workload/cappo-backend"


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if key in {"biscuit_token", "token", "nonce", "token_id"}:
        return "<REDACTED>"
    return value


def request(name: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    payload = json.dumps(body or {}, sort_keys=True).encode()
    save_json(REQ / f"{name}.json", redact(body or {}))
    req = urllib.request.Request(
        BASE + path,
        data=payload if method != "GET" else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['ACTIVATION_AUTH_TOKEN']}",
        },
    )
    context = ssl.create_default_context(cafile=str(CA))
    context.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
    try:
        with urllib.request.urlopen(req, context=context, timeout=15) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read()
    parsed = json.loads(raw.decode("utf-8"))
    save_json(RESP / f"{name}.json", redact(parsed))
    return status, parsed


def wait_ready() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            status, _ = request(f"health_{int(time.time() * 1000)}", "GET", "/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("server did not become ready")


def start_server(label: str) -> subprocess.Popen[bytes]:
    log = (LOGS / f"server_{label}.log").open("wb")
    process = subprocess.Popen(
        [PYTHON, str(RUNTIME)],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        start_new_session=True,
    )
    wait_ready()
    return process


def stop_server(process: subprocess.Popen[bytes]) -> None:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def mount_body() -> dict[str, Any]:
    return {
        "package_ref": PACKAGE,
        "execution_scope": {
            "workspace": WORKSPACE,
            "project": PROJECT,
            "reads": ["record.read"],
            "writes": ["record.create", "record.delete"],
            "resources": ["*"],
            "blocked": [],
        },
        "requested_action_scope": {
            "reads": ["record.read"],
            "writes": ["record.create", "record.delete"],
            "blocked": [],
        },
        "role": "ephemeral_executor",
        "policy": {"mode": "draft_only", "default": "deny"},
        "ttl_seconds": 300,
    }


def token_meta(response: dict[str, Any]) -> dict[str, Any]:
    token = response.get("token") or {}
    biscuit = token.get("biscuit_token")
    return {
        "biscuit_present": isinstance(biscuit, str) and bool(biscuit),
        "biscuit_sha256": hashlib.sha256(biscuit.encode()).hexdigest()
        if isinstance(biscuit, str)
        else None,
        "biscuit_length": len(biscuit) if isinstance(biscuit, str) else None,
        "token_id_present": bool(token.get("token_id")),
        "nonce_present": bool(token.get("nonce")),
        "mount_id": response.get("mount", {}).get("id"),
        "execution_id": token.get("execution_id"),
    }


def db_mount_meta(mount_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(DB)
    row = connection.execute(
        "SELECT token_json, terminated, nonce_consumed, owner_workspace "
        "FROM capability_mounts WHERE mount_id = ?",
        (mount_id,),
    ).fetchone()
    connection.close()
    if row is None:
        return {"row_present": False}
    token = json.loads(row[0])
    biscuit = token.get("biscuit_token")
    return {
        "row_present": True,
        "terminated": bool(row[1]),
        "nonce_consumed": bool(row[2]),
        "owner_workspace": row[3],
        "biscuit_present": isinstance(biscuit, str) and bool(biscuit),
        "biscuit_sha256": hashlib.sha256(biscuit.encode()).hexdigest()
        if isinstance(biscuit, str)
        else None,
        "biscuit_length": len(biscuit) if isinstance(biscuit, str) else None,
    }


def events_for(operation_id: str | None = None, mount_id: str | None = None) -> list[dict[str, Any]]:
    connection = sqlite3.connect(DB)
    query = (
        "SELECT event_id, operation_id, state, version, receipt_id, mount_id, "
        "action, resource, completion_proof_type, error_summary "
        "FROM consequence_execution_events"
    )
    params: tuple[Any, ...] = ()
    if operation_id is not None:
        query += " WHERE operation_id = ?"
        params = (operation_id,)
    elif mount_id is not None:
        query += " WHERE mount_id = ?"
        params = (mount_id,)
    query += " ORDER BY operation_id, version"
    rows = connection.execute(query, params).fetchall()
    connection.close()
    columns = [
        "event_id", "operation_id", "state", "version", "receipt_id", "mount_id",
        "action", "resource", "completion_proof_type", "error_summary",
    ]
    return [dict(zip(columns, row)) for row in rows]


def adapter_state() -> dict[str, Any]:
    path = ROOT / "adapter_state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def execute_body(response: dict[str, Any], action: str, resource: str, arguments: dict[str, Any], operation_id: str) -> dict[str, Any]:
    token = response["token"]
    return {
        "token_id": token["token_id"],
        "nonce": token["nonce"],
        "action": action,
        "target_ref": "activation.local-record",
        "resource": resource,
        "arguments": arguments,
        "operation_id": operation_id,
    }


def main() -> None:
    if len(os.environ.get("BISCUIT_ROOT_PRIVATE_KEY_HEX", "")) != 64:
        raise RuntimeError("BISCUIT_ROOT_PRIVATE_KEY_HEX must be exported in the parent shell")
    for path in (REQ, RESP, LOGS):
        path.mkdir(parents=True, exist_ok=True)
    process = start_server("initial")
    try:
        status, pre_mount = request("01_pre_gate_mount", "POST", "/v1/capability/mounts", mount_body())
        if status != 200:
            raise RuntimeError(f"pre-gate mount failed: {status} {pre_mount}")
        pre_meta = token_meta(pre_mount)
        pre_id = pre_meta["mount_id"]
        save_json(ROOT / "01_pre_gate_mount_metadata.json", pre_meta)
        save_json(ROOT / "01_pre_gate_db_metadata_before_restart.json", db_mount_meta(pre_id))
        stop_server(process)
        process = start_server("restart")
        pre_token = pre_mount["token"]
        pre_eval_body = execute_body(
            pre_mount, "record.read", "pre-gate-record", {}, "op-pre-gate"
        )
        status, pre_eval = request(
            "03_pre_gate_evaluate", "POST", f"/v1/capability/mounts/{pre_id}/execute", pre_eval_body
        )
        save_json(
            ROOT / "03_pre_gate_authority_metadata.json",
            {
                "status": status,
                "decision": pre_eval.get("decision"),
                "reason": pre_eval.get("reason"),
                "biscuit_verified_by_execution": pre_eval.get("reason") not in {
                    "missing_cryptographic_authority",
                    "lease_invariant_violation",
                },
                "mount": db_mount_meta(pre_id),
            },
        )

        status, mount_a = request("04_mount_a_create", "POST", "/v1/capability/mounts", mount_body())
        if status != 200:
            raise RuntimeError(f"mount A failed: {status} {mount_a}")
        mount_a_meta = token_meta(mount_a)
        mount_a_id = mount_a_meta["mount_id"]
        resource_a = "allowed-record"
        path_a = ROOT / "records" / f"{resource_a}.json"
        if path_a.exists():
            path_a.unlink()
        before_a = {"exists": path_a.exists(), "adapter": adapter_state()}
        body_a = execute_body(
            mount_a, "record.create", resource_a,
            {"content": "activation-v1-allowed", "mount": mount_a_id},
            "op-mount-a-create",
        )
        status, exec_a = request(
            "04_mount_a_execute", "POST", f"/v1/capability/mounts/{mount_a_id}/execute", body_a
        )
        after_a = {
            "exists": path_a.exists(),
            "content": path_a.read_text(encoding="utf-8") if path_a.exists() else None,
            "adapter": adapter_state(),
            "db_mount": db_mount_meta(mount_a_id),
            "events": events_for(operation_id="op-mount-a-create"),
        }
        save_json(ROOT / "04_mount_a_state.json", {"before": before_a, "after": after_a})
        status, replay_a = request(
            "05_mount_a_replay", "POST", f"/v1/capability/mounts/{mount_a_id}/execute", body_a
        )
        save_json(
            ROOT / "05_mount_a_replay_state.json",
            {"response_status": status, "response": replay_a, "adapter": adapter_state(), "events": events_for(operation_id="op-mount-a-create")},
        )

        resource_b = "protected-record"
        path_b = ROOT / "records" / f"{resource_b}.json"
        path_b.write_text('{"content":"must-survive-denial"}\n', encoding="utf-8")
        status, mount_b = request("06_mount_b_create", "POST", "/v1/capability/mounts", mount_body())
        if status != 200:
            raise RuntimeError(f"mount B failed: {status} {mount_b}")
        mount_b_meta = token_meta(mount_b)
        mount_b_id = mount_b_meta["mount_id"]
        before_b = {"exists": path_b.exists(), "content": path_b.read_text(encoding="utf-8"), "adapter": adapter_state()}
        body_b = execute_body(mount_b, "record.delete", resource_b, {}, "op-mount-b-delete")
        status, deny_b = request(
            "06_mount_b_delete", "POST", f"/v1/capability/mounts/{mount_b_id}/execute", body_b
        )
        after_b = {
            "exists": path_b.exists(),
            "content": path_b.read_text(encoding="utf-8") if path_b.exists() else None,
            "adapter": adapter_state(),
            "db_mount": db_mount_meta(mount_b_id),
            "events": events_for(operation_id="op-mount-b-delete"),
        }
        save_json(ROOT / "06_mount_b_state.json", {"before": before_b, "after": after_b})
        status, replay_b = request(
            "07_mount_b_replay", "POST", f"/v1/capability/mounts/{mount_b_id}/execute", body_b
        )
        save_json(
            ROOT / "07_mount_b_replay_state.json",
            {"response_status": status, "response": replay_b, "adapter": adapter_state(), "events": events_for(operation_id="op-mount-b-delete")},
        )
        stop_server(process)
        process = start_server("final")
        save_json(ROOT / "server_endpoint.json", {"base_url": BASE, "pid": process.pid, "transport": "direct uvicorn + SQLite + local mTLS harness"})
        process = None
    finally:
        if process is not None:
            stop_server(process)


if __name__ == "__main__":
    main()
