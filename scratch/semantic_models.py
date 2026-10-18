import hashlib
import json

def _digest_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

class CanonicalEffectRequest:
    def __init__(self, capability_id: str, operation: str, resource: str, arguments_digest: str, consequence_class: str, semantic_version: str):
        self.capability_id = capability_id
        self.operation = operation
        self.resource = resource
        self.arguments_digest = arguments_digest
        self.consequence_class = consequence_class
        self.semantic_version = semantic_version

    def digest(self) -> str:
        return _digest_payload({
            "arguments_digest": self.arguments_digest,
            "capability_id": self.capability_id,
            "consequence_class": self.consequence_class,
            "operation": self.operation,
            "resource": self.resource,
            "semantic_version": self.semantic_version,
        })

class AdapterBinding:
    def __init__(self, adapter_ref: str, adapter_version: str, provider_api_version: str, canonical_effect_digest: str, mapping_digest: str):
        self.adapter_ref = adapter_ref
        self.adapter_version = adapter_version
        self.provider_api_version = provider_api_version
        self.canonical_effect_digest = canonical_effect_digest
        self.mapping_digest = mapping_digest
