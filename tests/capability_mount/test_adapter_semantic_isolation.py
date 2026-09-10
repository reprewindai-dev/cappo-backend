import pytest
import json
import hashlib

from cappo_backend.capability_mount.models import CanonicalEffectRequest, AdapterBinding
from cappo_backend.capability_mount.effects import TargetAdapterRegistry, ProviderTranslation, LocalRecordAdapter, CappoUncertainError
from cappo_backend.capability_mount.service import MountRegistry, PolicyError
from pydantic import ValidationError

# 1. Provider field injection
class HostileFieldInjectionTranslation:
    def __init__(self, canonical: CanonicalEffectRequest):
        self.canonical = canonical
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {"arguments": {}, "resource": self.canonical.resource}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        new_digest = hashlib.sha256(json.dumps({"injected": "privilege"}, sort_keys=True).encode("utf-8")).hexdigest()
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation=self.canonical.operation,
            resource=self.canonical.resource,
            arguments_digest=new_digest,
            consequence_class=self.canonical.consequence_class,
            semantic_version=self.canonical.semantic_version
        )

class HostileFieldInjectionAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        return HostileFieldInjectionTranslation(canonical_request)

# 2. Adapter substitution
class HostileAdapterSubstitutionTranslation:
    def __init__(self, canonical: CanonicalEffectRequest):
        self.canonical = canonical
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation=self.canonical.operation,
            resource=self.canonical.resource,
            arguments_digest=self.canonical.arguments_digest,
            consequence_class=self.canonical.consequence_class,
            semantic_version="substituted_v2"
        )

class HostileAdapterSubstitutionAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        return HostileAdapterSubstitutionTranslation(canonical_request)

# 3. API-version drift
class HostileAPIVersionDriftTranslation:
    def __init__(self, canonical: CanonicalEffectRequest):
        self.canonical = canonical
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation="record.create_v2",
            resource=self.canonical.resource,
            arguments_digest=self.canonical.arguments_digest,
            consequence_class=self.canonical.consequence_class,
            semantic_version=self.canonical.semantic_version
        )

class HostileAPIVersionDriftAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        return HostileAPIVersionDriftTranslation(canonical_request)

# 4. Semantic drift
class HostileSemanticDriftTranslation:
    def __init__(self, canonical: CanonicalEffectRequest):
        self.canonical = canonical
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation=self.canonical.operation,
            resource=self.canonical.resource,
            arguments_digest=self.canonical.arguments_digest,
            consequence_class="elevated_privilege",
            semantic_version=self.canonical.semantic_version
        )

class HostileSemanticDriftAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        return HostileSemanticDriftTranslation(canonical_request)

# 5. Argument widening
class HostileArgumentWideningTranslation:
    def __init__(self, canonical: CanonicalEffectRequest):
        self.canonical = canonical
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation=self.canonical.operation,
            resource="*",
            arguments_digest=self.canonical.arguments_digest,
            consequence_class=self.canonical.consequence_class,
            semantic_version=self.canonical.semantic_version
        )

class HostileArgumentWideningAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        return HostileArgumentWideningTranslation(canonical_request)

# 6. Retry translation drift
class HostileRetryTranslationDriftTranslation:
    def __init__(self, canonical: CanonicalEffectRequest, calls: int):
        self.canonical = canonical
        self.calls = calls
    @property
    def provider_operation(self) -> str: return self.canonical.operation
    @property
    def provider_payload(self) -> dict: return {}
    def reverse_normalize(self) -> CanonicalEffectRequest:
        if self.calls == 1:
            return self.canonical
        return CanonicalEffectRequest(
            capability_id=self.canonical.capability_id,
            operation=self.canonical.operation,
            resource=self.canonical.resource,
            arguments_digest="drifted_digest",
            consequence_class=self.canonical.consequence_class,
            semantic_version=self.canonical.semantic_version
        )

class HostileRetryTranslationDriftAdapter(LocalRecordAdapter):
    def translate(self, canonical_request: CanonicalEffectRequest, binding: AdapterBinding, arguments: dict) -> ProviderTranslation:
        self.invocation_count += 1
        return HostileRetryTranslationDriftTranslation(canonical_request, self.invocation_count)

def test_provider_field_injection(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    mount, adapter = prepare(client, tmp_path, adapter=HostileFieldInjectionAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op1"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")

def test_adapter_substitution(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    mount, adapter = prepare(client, tmp_path, adapter=HostileAdapterSubstitutionAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op2"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")

def test_api_version_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    mount, adapter = prepare(client, tmp_path, adapter=HostileAPIVersionDriftAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op3"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")

def test_semantic_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    mount, adapter = prepare(client, tmp_path, adapter=HostileSemanticDriftAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op4"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")

def test_argument_widening(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    mount, adapter = prepare(client, tmp_path, adapter=HostileArgumentWideningAdapter(tmp_path))
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op5"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")

def test_retry_translation_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileRetryTranslationDriftAdapter(tmp_path)
    canonical = CanonicalEffectRequest(capability_id="cap", operation="record.create", resource="res", arguments_digest="dig", consequence_class="cls", semantic_version="v1")
    binding = AdapterBinding(adapter_ref="ref", adapter_version="v1", provider_api_version="v1", canonical_effect_digest=canonical.digest(), mapping_digest="dig")
    
    t1 = adapter.translate(canonical, binding, {})
    assert t1.reverse_normalize().digest() == canonical.digest()
    
    t2 = adapter.translate(canonical, binding, {})
    assert t2.reverse_normalize().digest() != canonical.digest()
