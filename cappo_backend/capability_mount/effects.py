"""Server-side adapters for capability-owned consequences."""

from __future__ import annotations

import json
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

    def _counter_path(self, resource: str) -> Path:
        validate_resource(resource)
        path = (self.root / f"counter_{resource}.json").resolve()
        if path.parent != self.root:
            raise ValueError("invalid_target_resource")
        return path

    @staticmethod
    def _initial_state() -> dict[str, int]:
        return {"value": 0, "version": 0}

    def _read_state(self, path: Path) -> dict[str, int]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._initial_state()

    @staticmethod
    def _write_state(path: Path, state: dict[str, int]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
        self.invocations_by_action[context.action] = (
            self.invocations_by_action.get(context.action, 0) + 1
        )
        path = self._counter_path(context.resource)
        state = self._read_state(path)

        if context.action == "counter.read":
            return {
                "resource": context.resource,
                "value": state["value"],
                "version": state["version"],
            }
        if context.action == "counter.increment":
            previous_value = state["value"]
            next_state = {
                "value": previous_value + 1,
                "version": state["version"] + 1,
            }
            self._write_state(path, next_state)
            return {
                "resource": context.resource,
                "previous_value": previous_value,
                "value": next_state["value"],
                "version": next_state["version"],
            }
        if context.action == "counter.reset":
            next_state = {"value": 0, "version": state["version"] + 1}
            self._write_state(path, next_state)
            return {
                "resource": context.resource,
                "value": next_state["value"],
                "version": next_state["version"],
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
