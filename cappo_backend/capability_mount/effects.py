"""Server-side adapters for capability-owned consequences."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Protocol

_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class CappoUncertainError(Exception):
    """Raised when an effect may have landed but its outcome cannot be determined."""


from dataclasses import dataclass

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

    def _record_path(self, resource: str) -> Path:
        validate_resource(resource)
        path = (self.root / f"{resource}.json").resolve()
        if path.parent != self.root:
            raise ValueError("invalid_target_resource")
        return path

    def dispatch(self, context: ConsequenceContext) -> object:
        self.invocation_count += 1
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


class TargetAdapterRegistry:
    """Registry of server-owned effect adapters."""

    def __init__(self) -> None:
        self._targets: dict[str, TargetAdapter] = {}

    def register(self, ref: str, adapter: TargetAdapter) -> None:
        self._targets[ref] = adapter

    def resolve(self, ref: str) -> TargetAdapter | None:
        return self._targets.get(ref)
