from pathlib import Path
import re

path = Path('cappo_backend/capability_mount/models.py')
content = path.read_text(encoding='utf-8')

# Remove the previously injected classes
pattern = r"import hashlib\nimport json\n\nclass CanonicalEffectRequest.*?class AdapterBinding.*?\n\n"
content = re.sub(pattern, "", content, flags=re.DOTALL)

# And add them at the bottom
additions = """
import hashlib
import json

class CanonicalEffectRequest(ContractModel):
    capability_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    arguments_digest: str = Field(min_length=1)
    consequence_class: str = Field(min_length=1)
    semantic_version: str = Field(min_length=1)

    def digest(self) -> str:
        payload = json.dumps(
            {
                "arguments_digest": self.arguments_digest,
                "capability_id": self.capability_id,
                "consequence_class": self.consequence_class,
                "operation": self.operation,
                "resource": self.resource,
                "semantic_version": self.semantic_version,
            },
            sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

class AdapterBinding(ContractModel):
    adapter_ref: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    provider_api_version: str = Field(min_length=1)
    canonical_effect_digest: str = Field(min_length=1)
    mapping_digest: str = Field(min_length=1)
"""

content += additions
path.write_text(content, encoding='utf-8')
print("Fixed models.py")
