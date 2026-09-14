import re
from pathlib import Path

content = Path('cappo_backend/capability_mount/models.py').read_text()

additions = '''
import hashlib
import json

class CanonicalEffectRequest(ContractModel):
    capability_id: str
    operation: str
    resource: str
    arguments_digest: str
    consequence_class: str
    semantic_version: str

    def digest(self) -> str:
        payload = json.dumps(
            {
                " arguments_digest:
