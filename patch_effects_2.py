import json
import re

with open("cappo_backend/capability_mount/effects.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add LocalRecordProfile right before TargetAdapterRegistry
profile = """
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
"""

content = content.replace("class TargetAdapterRegistry:", profile)

with open("cappo_backend/capability_mount/effects.py", "w", encoding="utf-8") as f:
    f.write(content)

