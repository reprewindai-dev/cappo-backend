import base64
import json
import time

def examine_seam():
    print("=== Trusted Assertion Seam Inspection ===")
    print("1. exec_router.py extracts `Veklom-Authority` using `_get_b64_json`")
    print("2. `_get_b64_json` ONLY does base64.b64decode() and json.loads()")
    print("3. No signature validation occurs before casting to AuthorityArtifact.")
    print("4. `biscuit_token` is extracted raw from `request.headers.get('Veklom-Authority')`")
    print("5. CapabilityHandler.execute() checks ONLY `if not ctx.biscuit_token: raise ...`")
    print("6. Orchestrator executes without further cryptographic checks of the token.")
    print("Result: SEAM IS BROKEN (INVALID)")

examine_seam()
