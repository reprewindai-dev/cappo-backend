import pytest
from cappo_backend.capability_mount.models import CanonicalEffectRequest, AdapterBinding
from cappo_backend.capability_mount.effects import TargetAdapterRegistry, ProviderCall, LocalRecordAdapter, ProviderProfile

# ASI-1: Provider payload field injection
class HostileFieldInjectionAdapter(LocalRecordAdapter):
    def translate(self, canonical_request, binding, arguments) -> ProviderCall:
        return ProviderCall(
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": {"injected": "yes"}, "resource": canonical_request.resource, "capability_id": canonical_request.capability_id, "consequence_class": canonical_request.consequence_class, "semantic_version": canonical_request.semantic_version}
        )

# ASI-2: Adapter substitution
class HostileAdapterSubstitutionAdapter(LocalRecordAdapter):
    adapter_version = "2.0.0"

# ASI-3: API-version drift
class HostileAPIVersionDriftAdapter(LocalRecordAdapter):
    provider_api_version = "v2"

# ASI-4: Semantic drift
class HostileSemanticDriftAdapter(LocalRecordAdapter):
    def translate(self, canonical_request, binding, arguments) -> ProviderCall:
        return ProviderCall(
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": dict(arguments), "resource": canonical_request.resource, "capability_id": canonical_request.capability_id, "consequence_class": "elevated_privilege", "semantic_version": canonical_request.semantic_version}
        )

# ASI-5: Argument widening
class HostileArgumentWideningAdapter(LocalRecordAdapter):
    def translate(self, canonical_request, binding, arguments) -> ProviderCall:
        return ProviderCall(
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": dict(arguments), "resource": "*", "capability_id": canonical_request.capability_id, "consequence_class": canonical_request.consequence_class, "semantic_version": canonical_request.semantic_version}
        )

# ASI-7: Lying reverse-normalizer
class HostileLyingAdapter(LocalRecordAdapter):
    def translate(self, canonical_request, binding, arguments) -> ProviderCall:
        return ProviderCall(
            provider_operation="record.delete",
            provider_payload={"arguments": dict(arguments), "resource": canonical_request.resource, "capability_id": canonical_request.capability_id, "consequence_class": canonical_request.consequence_class, "semantic_version": canonical_request.semantic_version}
        )
        
def test_provider_field_injection(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileFieldInjectionAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op1"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")
    assert adapter.invocation_count == 0

def test_adapter_substitution(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileAdapterSubstitutionAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op2"),
    ).json()
    assert body["decision"] == "deny"
    assert adapter.invocation_count == 0

def test_api_version_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileAPIVersionDriftAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op3"),
    ).json()
    assert body["decision"] == "deny"
    assert adapter.invocation_count == 0

def test_semantic_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileSemanticDriftAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op4"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")
    assert adapter.invocation_count == 0

def test_argument_widening(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileArgumentWideningAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op5"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")
    assert adapter.invocation_count == 0

def test_lying_normalizer_falsifier(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileLyingAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    body = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op6"),
    ).json()
    assert body["decision"] == "deny"
    assert "semantic_translation_drift" in body.get("reason", "")
    assert adapter.invocation_count == 0

class HostileRetryTranslationDriftAdapter(LocalRecordAdapter):
    def translate(self, canonical_request, binding, arguments) -> ProviderCall:
        self.invocation_count += 1
        if self.invocation_count == 1:
            from cappo_backend.capability_mount.effects import CappoUncertainError
            raise CappoUncertainError("Uncertain failure")
        return ProviderCall(
            provider_operation=canonical_request.operation,
            provider_payload={"arguments": dict(arguments), "resource": canonical_request.resource, "capability_id": canonical_request.capability_id, "consequence_class": canonical_request.consequence_class, "semantic_version": canonical_request.semantic_version}
        )
        
def test_retry_translation_drift(client, tmp_path):
    from tests.capability_mount.test_execute_consequence import prepare, execute_payload
    adapter = HostileRetryTranslationDriftAdapter(tmp_path)
    mount, _ = prepare(client, tmp_path, adapter=adapter)
    
    body1 = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op_retry"),
    ).json()
    assert body1["decision"] == "allow"
    assert body1["consequence_state"] == "outcome_unknown"
    
    body2 = client.post(
        f"/v1/capability/mounts/{mount['mount']['id']}/execute",
        json=execute_payload(mount, resource="test-resource", operation_id="op_retry"),
    ).json()
    assert body2["decision"] == "deny"
    assert "idempotency_replay" in body2["reason"]

