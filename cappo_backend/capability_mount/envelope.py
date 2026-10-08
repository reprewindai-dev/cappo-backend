"""The consequence envelope: one digest for the exact operation a mount may perform.

A mount requested for a bound operation (an ABIDE contract step) carries that
operation in ``execution_scope.operation``. CAPPO computes this digest itself, puts
it in the mount's grants and in the Biscuit (``allowed_envelope``), and refuses any
execute whose target, action, resource or arguments hash differently. The caller
never supplies the digest, only the operation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

ENVELOPE_DOMAIN = "veklom-consequence-envelope-v1"


def operation_envelope_digest(
    *, package_ref: str, target_ref: str, action: str, resource: str, arguments: dict[str, Any]
) -> str:
    """SHA-256 over one explicit canonical JSON form (sorted keys, no whitespace, UTF-8).

    Key order never changes the digest; any change of package, target, action,
    resource or any argument name or value does. Never ``str(dict)``.
    """
    body = json.dumps(
        {"action": action, "arguments": arguments, "package_ref": package_ref, "resource": resource,
         "target_ref": target_ref},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(f"{ENVELOPE_DOMAIN}\n{body}".encode("utf-8")).hexdigest()
