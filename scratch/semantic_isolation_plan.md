# Implementation Plan: Adapter Semantic Isolation

1. **models.py additions**:
   - CanonicalEffectRequest(capability_id, operation, resource, arguments_digest, consequence_class, semantic_version)
   - AdapterBinding(adapter_ref, adapter_version, provider_api_version, canonical_effect_digest, mapping_digest)

2. **effects.py protocol changes**:
   - The TargetAdapter protocol needs a way to declare its translation.
   - We introduce a ProviderTranslation object that has a everse_normalize() method.
   - When service.py dispatches, it calls dapter.translate().
   - The runtime asserts 	ranslation.reverse_normalize().digest() == canonical_request.digest().
   - Then it executes the translation.

3. **	est_adapter_semantic_isolation.py**:
   - Implement hostile adapters that intentionally return a ProviderTranslation whose everse_normalize() digest does not match, or they swap the adapter ref, or they change the argument payload.
