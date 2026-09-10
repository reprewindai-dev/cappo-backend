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

@dataclass(frozen=True)
class ProviderCall:
    provider_operation: str
    provider_payload: Mapping[str, object]

class ProviderProfile(Protocol):
    normalizer_identity: str
    mapping_version: str
    def normalize_provider_call(self, call: ProviderCall, binding: AdapterBinding) -> CanonicalEffectRequest: ...

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
    ) -> ProviderCall:
        """Translate into a provider-specific operation."""

    def execute_translation(self, call: ProviderCall) -> object:
        """Invoke one registered, capability-owned effect."""


def validate_resource(resource: str) -> None:
    if not _RESOURCE_PATTERN.fullmatch(resource):
        raise ValueError("invalid_target_resource")




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
    ) -> ProviderCall:
        return ProviderCall(
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": dict(arguments), "resource": canonical_request.resource, "capability_id": canonical_request.capability_id, "consequence_class": canonical_request.consequence_class, "semantic_version": canonical_request.semantic_version}
        )

    def execute_translation(self, call: ProviderCall) -> object:
        self.invocation_count += 1
        path = self._record_path(str(call.provider_payload["resource"]))
        op = call.provider_operation
        
        if op == "record.create":
            document = dict(call.provider_payload["arguments"]) # type: ignore
            path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            return document
        if op == "record.read":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise KeyError(call.provider_payload["resource"]) from exc
        if op == "record.delete":
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise KeyError(call.provider_payload["resource"]) from exc
            return {"deleted": call.provider_payload["resource"]}
        raise ValueError("target_not_mapped")



class LocalRecordProfile:
    normalizer_identity = "activation.local-record.normalizer"
    mapping_version = "1.0.0"

    def normalize_provider_call(self, call: ProviderCall, binding: AdapterBinding) -> CanonicalEffectRequest:
        import hashlib
        args = dict(call.provider_payload.get("arguments", {}))
        resource = str(call.provider_payload.get("resource", ""))
        capability_id = str(call.provider_payload.get("capability_id", ""))
        consequence_class = str(call.provider_payload.get("consequence_class", ""))
        semantic_version = str(call.provider_payload.get("semantic_version", ""))
        
        args_digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode("utf-8")).hexdigest()
        
        return CanonicalEffectRequest(
            capability_id=capability_id,
            operation=call.provider_operation,
            resource=resource,
            arguments_digest=args_digest,
            consequence_class=consequence_class,
            semantic_version=semantic_version
        )

class TargetAdapterRegistry:

    """Registry of server-owned effect adapters."""

    def __init__(self) -> None:
        self._targets: dict[str, TargetAdapter] = {}
        self._profiles: dict[str, ProviderProfile] = {}

    def register(self, ref: str, adapter: TargetAdapter, profile: ProviderProfile = None) -> None:
        self._targets[ref] = adapter
        if profile:
            self._profiles[ref] = profile

    def resolve(self, ref: str) -> TargetAdapter | None:
        return self._targets.get(ref)
        
    def resolve_profile(self, ref: str) -> ProviderProfile | None:
        return self._profiles.get(ref)
