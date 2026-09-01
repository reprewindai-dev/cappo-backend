"""Server-side adapters for capability-owned consequences."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Protocol

_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class CappoUncertainError(Exception):
    """Raised when an effect may have landed but its outcome cannot be determined."""


class EffectAdapter(Protocol):
    actions: frozenset[str]
    invocation_count: int
    invocations_by_action: Mapping[str, int]

    def invoke(
        self,
        action: str,
        resource: str,
        arguments: Mapping[str, object],
    ) -> object:
        """Invoke one registered, capability-owned effect."""


def validate_resource(resource: str) -> None:
    if not _RESOURCE_PATTERN.fullmatch(resource):
        raise ValueError("invalid_effect_resource")


class LocalRecordAdapter(EffectAdapter):
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
            raise ValueError("invalid_effect_resource")
        return path

    def invoke(
        self,
        action: str,
        resource: str,
        arguments: Mapping[str, object],
    ) -> object:
        self.invocation_count += 1
        self.invocations_by_action[action] = self.invocations_by_action.get(action, 0) + 1
        path = self._record_path(resource)
        if action == "record.create":
            document = dict(arguments)
            path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            return document
        if action == "record.read":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise KeyError(resource) from exc
        if action == "record.delete":
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise KeyError(resource) from exc
            return {"deleted": resource}
        raise ValueError("effect_not_mapped")


class EffectTargetRegistry:
    """Registry of server-owned effect adapters."""

    def __init__(self) -> None:
        self._targets: dict[str, EffectAdapter] = {}

    def register(self, ref: str, adapter: EffectAdapter) -> None:
        self._targets[ref] = adapter

    def resolve(self, ref: str) -> EffectAdapter | None:
        return self._targets.get(ref)
