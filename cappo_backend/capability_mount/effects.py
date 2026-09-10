"""Server-side adapters for capability-owned consequences."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Protocol
from dataclasses import dataclass

from cappo_backend.capability_mount.models import CanonicalEffectRequest, AdapterBinding

_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

class CappoUncertainError(Exception):
    """Raised when an effect may have landed but its outcome cannot be determined."""

class ProviderTranslation(Protocol):
    @property
    def provider_operation(self) -> str: ...
    @property
    def provider_payload(self) -> Mapping[str, object]: ...
    def reverse_normalize(self) -> CanonicalEffectRequest: ...

class TargetAdapter(Protocol):
    ref: str
    adapter_version: str
    provider_api_version: str
    actions: frozenset[str]
    invocation_count: int

    def translate(
        self,
        canonical_request: CanonicalEffectRequest,
        binding: AdapterBinding,
        arguments: Mapping[str, object],
    ) -> ProviderTranslation:
        """Translate into a provider-specific operation."""

    def execute_translation(self, translation: ProviderTranslation) -> object:
        """Invoke one registered, capability-owned effect."""


def validate_resource(resource: str) -> None:
    if not _RESOURCE_PATTERN.fullmatch(resource):
        raise ValueError("invalid_target_resource")


@dataclass(frozen=True)
class LocalRecordTranslation:
    canonical_request: CanonicalEffectRequest
    provider_operation: str
    provider_payload: Mapping[str, object]
    
    def reverse_normalize(self) -> CanonicalEffectRequest:
        return self.canonical_request


class LocalRecordAdapter(TargetAdapter):
    """Activation v1 file-backed record adapter."""

    ref = "activation.local-record"
    adapter_version = "1.0.0"
    provider_api_version = "v1"
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

    def translate(
        self,
        canonical_request: CanonicalEffectRequest,
        binding: AdapterBinding,
        arguments: Mapping[str, object],
    ) -> ProviderTranslation:
        return LocalRecordTranslation(
            canonical_request=canonical_request,
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": dict(arguments), "resource": canonical_request.resource}
        )

    def execute_translation(self, translation: ProviderTranslation) -> object:
        self.invocation_count += 1
        path = self._record_path(str(translation.provider_payload["resource"]))
        op = translation.provider_operation
        
        if op == "record.create":
            document = dict(translation.provider_payload["arguments"]) # type: ignore
            path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            return document
        if op == "record.read":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise KeyError(translation.provider_payload["resource"]) from exc
        if op == "record.delete":
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise KeyError(translation.provider_payload["resource"]) from exc
            return {"deleted": translation.provider_payload["resource"]}
        raise ValueError("target_not_mapped")


class TargetAdapterRegistry:
    """Registry of server-owned effect adapters."""

    def __init__(self) -> None:
        self._targets: dict[str, TargetAdapter] = {}

    def register(self, ref: str, adapter: TargetAdapter) -> None:
        self._targets[ref] = adapter

    def resolve(self, ref: str) -> TargetAdapter | None:
        return self._targets.get(ref)
