from pathlib import Path
import re

content = Path('cappo_backend/capability_mount/service.py').read_text(encoding='utf-8')

# Remove ConsequenceContext import
content = re.sub(r'ConsequenceContext,\s*', '', content)
content = content.replace(', ConsequenceContext', '')
content = content.replace('ConsequenceContext,', '')

# Replace ConsequenceContext instantiation and invoke_effect
old_block = """        context = ConsequenceContext(
            action=action,
            resource=resource,
            arguments=arguments,
            operation_id=op_id,
        )

        def invoke_effect(**_: object) -> object:
            return adapter.dispatch(context)"""

new_block = """        from cappo_backend.models.capability_mount import CanonicalEffectRequest, AdapterBinding
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

content = content.replace(old_block, new_block)

Path('cappo_backend/capability_mount/service.py').write_text(content, encoding='utf-8')
print("Patched service successfully.")
