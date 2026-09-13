"""Server-side adapters for capability-owned consequences."""

from __future__ import annotations

import json
import sqlite3
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from uuid import uuid4

_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class CappoUncertainError(Exception):
    """Raised when an effect may have landed but its outcome cannot be determined."""


@dataclass(frozen=True)
class ConsequenceContext:
    action: str
    resource: str
    arguments: Mapping[str, object]
    operation_id: str | None
    workspace: str | None = None
    workspace: str | None = None


class TargetAdapter(Protocol):
    actions: frozenset[str]
    invocation_count: int

    def dispatch(self, context: ConsequenceContext) -> object:
        """Invoke one registered, capability-owned effect."""


def validate_resource(resource: str) -> None:
    if not _RESOURCE_PATTERN.fullmatch(resource):
        raise ValueError("invalid_target_resource")


class LocalRecordAdapter(TargetAdapter):
    """Activation v1 file-backed record adapter."""

    ref = "activation.local-record"
    actions = frozenset({"record.create", "record.read", "record.delete"})

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.invocation_count = 0
        self.invocations_by_action: dict[str, int] = {}

    def _record_path(self, resource: str) -> Path:
        validate_resource(resource)
        path = (self.root / f"{resource}.json").resolve()
        if path.parent != self.root:
            raise ValueError("invalid_target_resource")
        return path

    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
        self.invocations_by_action[context.action] = (
            self.invocations_by_action.get(context.action, 0) + 1
        )
        path = self._record_path(context.resource)
        if context.action == "record.create":
            document = dict(context.arguments)
            path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            return document
        if context.action == "record.read":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise KeyError(context.resource) from exc
        if context.action == "record.delete":
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise KeyError(context.resource) from exc
            return {"deleted": context.resource}
        raise ValueError("target_not_mapped")


class GovernedCounterAdapter(TargetAdapter):
    """Sandbox reference capability for one-step governed counter mutations."""

    ref = "activation.governed-counter"
    actions = frozenset({"counter.read", "counter.increment", "counter.reset"})

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.invocation_count = 0
        self.invocations_by_action: dict[str, int] = {}
        self.db_path = self.root / "governed_counters.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS counters (
                    workspace TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    value INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (workspace, resource)
                )
            """)

    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
        self.invocations_by_action[context.action] = (
            self.invocations_by_action.get(context.action, 0) + 1
        )
        
        validate_resource(context.resource)
        
        if not context.workspace:
            raise ValueError("missing_workspace_identity")

        with sqlite3.connect(self.db_path, timeout=15.0, isolation_level="IMMEDIATE") as conn:
            cursor = conn.cursor()
            
            # Ensure row exists
            cursor.execute("""
                INSERT OR IGNORE INTO counters (workspace, resource, value, version)
                VALUES (?, ?, 0, 0)
            """, (context.workspace, context.resource))
            
            if context.action == "counter.read":
                cursor.execute('SELECT value, version FROM counters WHERE workspace = ? AND resource = ?', 
                               (context.workspace, context.resource))
                row = cursor.fetchone()
                return {
                    "resource": context.resource,
                    "value": row[0],
                    "version": row[1],
                }

            if context.action == "counter.increment":
                cursor.execute('SELECT value, version FROM counters WHERE workspace = ? AND resource = ?', 
                               (context.workspace, context.resource))
                row = cursor.fetchone()
                previous_value = row[0]
                
                cursor.execute("""
                    UPDATE counters 
                    SET value = value + 1, version = version + 1 
                    WHERE workspace = ? AND resource = ?
                    RETURNING value, version
                """, (context.workspace, context.resource))
                row = cursor.fetchone()
                value = row[0]
                version = row[1]
                return {
                    "resource": context.resource,
                    "previous_value": previous_value,
                    "value": value,
                    "version": version,
                }

            if context.action == "counter.reset":
                cursor.execute("""
                    UPDATE counters 
                    SET value = 0, version = version + 1 
                    WHERE workspace = ? AND resource = ?
                    RETURNING value, version
                """, (context.workspace, context.resource))
                row = cursor.fetchone()
                return {
                    "resource": context.resource,
                    "value": 0,
                    "version": row[1],
                }

        raise ValueError("target_not_mapped")


class TargetAdapterRegistry:
    """Registry of server-owned effect adapters."""

    def __init__(self) -> None:
        self._targets: dict[str, TargetAdapter] = {}

    def register(self, ref: str, adapter: TargetAdapter) -> None:
        self._targets[ref] = adapter

    def resolve(self, ref: str) -> TargetAdapter | None:
        return self._targets.get(ref)
