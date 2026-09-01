"""Capture the post-verification Biscuit authority-denial proof."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("ACTIVATION_V1_ROOT", "/home/ubuntu/activation_v1"))
DB = ROOT / "db" / "authority-denial.db"
RECORDS = ROOT / "records-authority-denial"
BASE = "https://127.0.0.1:8444"
PYTHON = "/home/ubuntu/repos/cappo-backend/.venv/bin/python"
SERVER = ROOT / "authority_denial_server.py"
CA = ROOT / "mtls" / "ca.crt"
CERT = ROOT / "mtls" / "client.crt"
KEY = ROOT / "mtls" / "client.key"
WORKSPACE = "workspace-activation-v1"


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def redact(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if key in {"biscuit_token", "token", "token_id", "nonce"}:
        return "<REDACTED>"
    return value


def call(name: str, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    save(ROOT / "requests" / f"authority_{name}.json", redact(body or {}))
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}, sort_keys=True).encode() if method != "GET" else None,
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['ACTIVATION_AUTH_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    context = ssl.create_default_context(cafile=str(CA))
    context.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
    try:
        with urllib.request.urlopen(req, context=context, timeout=15) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    parsed = json.loads(raw.decode())
    save(ROOT / "responses" / f"authority_{name}.json", redact(parsed))
    return status, parsed


def ready() -> None:
    for _ in range(100):
        try:
            if call("health", "GET", "/health")[0] == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("authority-denial server did not become ready")


def metadata(response: dict[str, Any]) -> dict[str, Any]:
    biscuit = response["token"].get("biscuit_token")
    return {
        "biscuit_present": bool(biscuit),
        "biscuit_length": len(biscuit),
        "biscuit_sha256": hashlib.sha256(biscuit.encode()).hexdigest(),
        "mount_id": response["mount"]["id"],
        "execution_id": response["token"]["execution_id"],
    }


def db_metadata(mount_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(DB)
    row = connection.execute(
        "SELECT token_json, terminated, nonce_consumed FROM capability_mounts WHERE mount_id = ?",
        (mount_id,),
    ).fetchone()
    connection.close()
    token = json.loads(row[0])
    biscuit = token["biscuit_token"]
    return {
        "biscuit_present": bool(biscuit),
        "biscuit_length": len(biscuit),
        "biscuit_sha256": hashlib.sha256(biscuit.encode()).hexdigest(),
        "terminated": bool(row[1]),
        "nonce_consumed": bool(row[2]),
    }


def main() -> None:
    env = os.environ.copy()
    if not env.get("ACTIVATION_AUTH_TOKEN") or not env.get("BISCUIT_ROOT_PRIVATE_KEY_HEX"):
        inherited = dict(
            item.split("=", 1)
            for item in Path("/proc/16526/environ").read_bytes().decode().split("\0")
            if "=" in item
        )
        for name, value in inherited.items():
            if name in {
                "AUTH_ENABLED",
                "JWT_AUTH_ENABLED",
                "JWT_ISSUER",
                "JWT_AUDIENCE",
                "JWT_PUBLIC_VERIFICATION_KEY",
                "ACTIVATION_AUTH_TOKEN",
                "ACTIVATION_JWT_PRIVATE_HEX",
                "BISCUIT_ROOT_PRIVATE_KEY_HEX",
                "ENFORCE_SPIFFE",
                "SPIFFE_TRUST_DOMAIN",
                "CAPABILITY_PACKAGES_JSON",
                "ENVIRONMENT",
                "CAPPO_REQUIRE_PERSISTENT_PGL",
            }:
                env[name] = value
    if len(env.get("BISCUIT_ROOT_PRIVATE_KEY_HEX", "")) != 64:
        raise RuntimeError("stable Biscuit root key was not available")
    if not env.get("ACTIVATION_JWT_PRIVATE_HEX"):
        raise RuntimeError("runtime JWT signing key was not available")
    from datetime import datetime, timedelta, timezone

    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    env["ACTIVATION_AUTH_TOKEN"] = jwt.encode(
        {
            "sub": "activation-operator",
            "workspace_id": WORKSPACE,
            "iss": env.get("JWT_ISSUER", "activation-v1"),
            "aud": env.get("JWT_AUDIENCE", "activation-runtime"),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(env["ACTIVATION_JWT_PRIVATE_HEX"])
        ),
        algorithm="EdDSA",
    )
    os.environ.update(
        {
            "ACTIVATION_AUTH_TOKEN": env["ACTIVATION_AUTH_TOKEN"],
            "BISCUIT_ROOT_PRIVATE_KEY_HEX": env["BISCUIT_ROOT_PRIVATE_KEY_HEX"],
        }
    )
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{DB}",
            "CAPABILITY_EFFECT_RECORD_ROOT": str(RECORDS),
            "PORT": "8444",
            "HOST": "127.0.0.1",
        }
    )
    RECORDS.mkdir(parents=True, exist_ok=True)
    (ROOT / "requests").mkdir(exist_ok=True)
    (ROOT / "responses").mkdir(exist_ok=True)
    process = subprocess.Popen(
        [PYTHON, str(SERVER)],
        cwd=str(ROOT),
        env=env,
        stdout=(ROOT / "logs" / "server_authority_denial.log").open("ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        ready()
        mount_body = {
            "package_ref": "activation@v1",
            "execution_scope": {
                "workspace": WORKSPACE,
                "project": "activation-v1",
                "reads": ["record.read"],
                "writes": ["record.create"],
                "resources": ["*"],
                "blocked": [],
            },
            "requested_action_scope": {
                "reads": ["record.read"],
                "writes": ["record.create"],
                "blocked": [],
            },
            "role": "ephemeral_executor",
            "policy": {"mode": "draft_only", "default": "deny"},
            "ttl_seconds": 300,
        }
        status, mounted = call("mount", "POST", "/v1/capability/mounts", mount_body)
        if status != 200 or mounted.get("decision") != "allow":
            raise RuntimeError(f"mount failed: {status} {mounted}")
        mount_id = mounted["mount"]["id"]
        save(ROOT / "08_authority_denial_mount_metadata.json", metadata(mounted))
        save(ROOT / "08_authority_denial_db_metadata_before.json", db_metadata(mount_id))
        token = mounted["token"]
        execute = {
            "token_id": token["token_id"],
            "nonce": token["nonce"],
            "action": "record.create",
            "target_ref": "activation.local-record",
            "resource": "authority-denied-record",
            "arguments": {"content": "must-not-write"},
            "operation_id": "op-authority-denial",
        }
        _, result = call(
            "execute",
            "POST",
            f"/v1/capability/mounts/{mount_id}/execute",
            execute,
        )
        state = {
            "response": redact(result),
            "adapter_state": json.loads((ROOT / "adapter_state.json").read_text()),
            "db_mount": db_metadata(mount_id),
            "record_exists": (RECORDS / "authority-denied-record.json").exists(),
            "record_path": str(RECORDS / "authority-denied-record.json"),
        }
        save(ROOT / "08_authority_denial_state.json", state)
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
