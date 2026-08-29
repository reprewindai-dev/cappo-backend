"""Low-risk governed connector for one exact sandbox append resource."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class ConnectorDenied(ValueError):
    """The request is not covered by the presented authority."""


class ConnectorConflict(RuntimeError):
    """An execution identity was reused with different action data."""


@dataclass(frozen=True)
class AppendReceipt:
    connector_id: str
    execution_id: str
    operation_id: str
    resource: str
    action_hash: str
    record_hash: str
    bytes_written: int
    timestamp: str
    compensates_execution_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxFileAppendConnector:
    """Append JSONL evidence records to one preconfigured regular file.

    Compensation appends a governed tombstone. Historical evidence is never
    rewritten or deleted.
    """

    connector_id = "sandbox_file_append"
    append_action = "fs:append"
    compensate_action = "fs:append:compensate"

    def __init__(self, target_path: str | Path, max_content_bytes: int = 4096) -> None:
        requested = Path(target_path).expanduser()
        requested.parent.mkdir(parents=True, exist_ok=True)
        self.path = requested.resolve(strict=False)
        self._parent = self.path.parent.resolve(strict=True)
        self.max_content_bytes = max_content_bytes
        self._lock = threading.RLock()

    @property
    def resource(self) -> str:
        return f"sandbox-file:{self.path.as_posix()}"

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> bytes:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _assert_safe_target(self) -> None:
        if self.path.parent.resolve(strict=True) != self._parent:
            raise ConnectorDenied("DENY: sandbox parent changed")
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise ConnectorDenied("DENY: sandbox target must be a regular non-symlink file")
        if self.path.exists() and self.path.stat().st_nlink != 1:
            raise ConnectorDenied("DENY: sandbox target cannot have hard links")

    @staticmethod
    def _claim_set(claims: Mapping[str, Any], claim: str) -> set[str]:
        value = claims.get(claim)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConnectorDenied(f"DENY: malformed {claim} claim")
        return set(value)

    def _authorize(self, claims: Mapping[str, Any], action: str) -> str:
        execution_id = claims.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise ConnectorDenied("DENY: missing execution_id")
        if action not in self._claim_set(claims, "allowed_actions"):
            raise ConnectorDenied("DENY: action is outside lease scope")
        if self.resource not in self._claim_set(claims, "allowed_resources"):
            raise ConnectorDenied("DENY: resource is outside lease scope")
        return execution_id

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def reconcile(self, execution_id: str, action_hash: str | None = None) -> dict | None:
        """Return positive evidence; absence is not proof that an action failed."""
        with self._lock:
            self._assert_safe_target()
            for record in self._records():
                if record.get("execution_id") != execution_id:
                    continue
                if action_hash and record.get("action_hash") != action_hash:
                    raise ConnectorConflict("execution_id reused with different action data")
                return record
        return None

    def _append_record(self, body: dict[str, Any]) -> AppendReceipt:
        record_hash = hashlib.sha256(self._canonical(body)).hexdigest()
        receipt = AppendReceipt(
            connector_id=self.connector_id,
            execution_id=body["execution_id"],
            operation_id=body["operation_id"],
            resource=self.resource,
            action_hash=body["action_hash"],
            record_hash=record_hash,
            bytes_written=0,
            timestamp=body["timestamp"],
            compensates_execution_id=body.get("compensates_execution_id"),
        )
        payload = b""
        for _ in range(3):
            receipt = AppendReceipt(**{**receipt.as_dict(), "bytes_written": len(payload)})
            candidate = self._canonical(
                {**body, "record_hash": record_hash, "receipt": receipt.as_dict()}
            ) + b"\n"
            if len(candidate) == receipt.bytes_written:
                payload = candidate
                break
            payload = candidate
        receipt = AppendReceipt(**{**receipt.as_dict(), "bytes_written": len(payload)})
        payload = self._canonical(
            {**body, "record_hash": record_hash, "receipt": receipt.as_dict()}
        ) + b"\n"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("short append write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return receipt

    def append(self, claims: Mapping[str, Any], content: str) -> AppendReceipt:
        execution_id = self._authorize(claims, self.append_action)
        if not isinstance(content, str) or not content:
            raise ConnectorDenied("DENY: content must be a non-empty string")
        if len(content.encode("utf-8")) > self.max_content_bytes:
            raise ConnectorDenied("DENY: content exceeds connector byte limit")
        action = {
            "connector_id": self.connector_id,
            "action": self.append_action,
            "execution_id": execution_id,
            "resource": self.resource,
            "content": content,
        }
        action_hash = hashlib.sha256(self._canonical(action)).hexdigest()
        with self._lock:
            existing = self.reconcile(execution_id, action_hash)
            if existing:
                return AppendReceipt(**existing["receipt"])
            body = {
                **action,
                "operation_id": f"append:{execution_id}",
                "action_hash": action_hash,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            return self._append_record(body)

    def compensate(
        self, claims: Mapping[str, Any], compensates_execution_id: str
    ) -> AppendReceipt:
        execution_id = self._authorize(claims, self.compensate_action)
        with self._lock:
            original = self.reconcile(compensates_execution_id)
            if not original or original.get("action") != self.append_action:
                raise ConnectorDenied("DENY: original append is not proven")
            action = {
                "connector_id": self.connector_id,
                "action": self.compensate_action,
                "execution_id": execution_id,
                "resource": self.resource,
                "compensates_execution_id": compensates_execution_id,
            }
            action_hash = hashlib.sha256(self._canonical(action)).hexdigest()
            existing = self.reconcile(execution_id, action_hash)
            if existing:
                return AppendReceipt(**existing["receipt"])
            return self._append_record(
                {
                    **action,
                    "operation_id": f"compensate:{execution_id}",
                    "action_hash": action_hash,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            )


__all__ = [
    "AppendReceipt",
    "ConnectorConflict",
    "ConnectorDenied",
    "SandboxFileAppendConnector",
]
