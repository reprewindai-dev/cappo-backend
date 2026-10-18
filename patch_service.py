import re

with open("cappo_backend/capability_mount/service.py", "r", encoding="utf-8") as f:
    content = f.read()

target = """        from cappo_backend.capability_mount.models import CanonicalEffectRequest, AdapterBinding
        import hashlib
        import json

        args_digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode("utf-8")).hexdigest()
        
        canonical_request = CanonicalEffectRequest(
            capability_id=str(record.mount.capability_id) if hasattr(record.mount, "capability_id") else "test-cap",
            operation=action,
            resource=resource,
            arguments_digest=args_digest,
            consequence_class=getattr(record.mount, "consequence_class", "default") or "default",
            semantic_version="1.0"
        )
        
        binding = AdapterBinding(
            adapter_ref=target_ref,
            adapter_version=getattr(adapter, "adapter_version", "1.0"),
            provider_api_version=getattr(adapter, "provider_api_version", "v1"),
            canonical_effect_digest=canonical_request.digest(),
            mapping_digest="synthetic-test-mapping"
        )

        def invoke_effect(**_: object) -> object:
            translation = adapter.translate(canonical_request, binding, arguments)
            
            # The Semantic Isolation Integrity Check:
            # Reversing the provider translation must yield a digest identical to the
            # canonical effect request that was authorized.
            if translation.reverse_normalize().digest() != canonical_request.digest():
                raise PolicyError("semantic_translation_drift")
            
            return adapter.execute_translation(translation)"""

replacement = """        from cappo_backend.capability_mount.models import CanonicalEffectRequest, AdapterBinding
        import hashlib
        import json

        args_digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode("utf-8")).hexdigest()
        
        package = self.packages.get(record.mount.package_ref)
        if package is None:
            raise PolicyError("missing_binding_metadata")
            
        consequence_class = str(package.policy_defaults.get("consequence_class", "default"))
        semantic_version = str(package.policy_defaults.get("semantic_version", "1.0"))
        
        if not consequence_class or not semantic_version:
            raise PolicyError("missing_binding_metadata")

        canonical_request = CanonicalEffectRequest(
            capability_id=record.mount.package_ref,
            operation=action,
            resource=resource,
            arguments_digest=args_digest,
            consequence_class=consequence_class,
            semantic_version=semantic_version
        )
        
        profile = self.target_adapters.resolve_profile(target_ref)
        if profile is None:
            raise PolicyError("missing_binding_metadata")

        binding = AdapterBinding(
            adapter_ref=target_ref,
            adapter_version=getattr(adapter, "adapter_version", "1.0"),
            provider_api_version=getattr(adapter, "provider_api_version", "v1"),
            canonical_effect_digest=canonical_request.digest(),
            mapping_digest=profile.mapping_version,
            normalizer_identity=profile.normalizer_identity
        )

        def invoke_effect(**_: object) -> object:
            call = adapter.translate(canonical_request, binding, arguments)
            
            # The Semantic Isolation Integrity Check:
            # CAPPO-owned ProviderProfile normalizes the provider call
            normalized_request = profile.normalize_provider_call(call, binding)
            
            if normalized_request.digest() != canonical_request.digest():
                raise PolicyError("semantic_translation_drift")
            
            return adapter.execute_call(call)"""

if target in content:
    content = content.replace(target, replacement)
else:
    print("TARGET NOT FOUND IN service.py")

with open("cappo_backend/capability_mount/service.py", "w", encoding="utf-8") as f:
    f.write(content)

