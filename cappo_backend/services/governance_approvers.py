"""Server-side approver resolution for governance v2 quarantine controls.

The quarantine approve/deny surface is a consequence boundary: an approval
releases a request that the Safety layer held back. Every input to that
decision must therefore come from the server's own verified state, never
from the request body or client-supplied headers:

- **Identity** is the principal ``AuthMiddleware`` placed in the ASGI scope
  after verifying the credential. For session callers (LockerPhycer-minted
  JWT) that is the token subject; for API-key callers it is the key's
  fingerprint form (``api-key:<sha256>``).
- **Workspace** is ``auth_workspace`` from the same scope, bound to the
  credential (JWT ``workspace_id`` claim, or the mount's owner workspace).
- **Trust** is looked up in the server-held approver registry
  (``Settings.governance_approvers``). An identity absent from the registry
  has trust 0 and cannot approve. A request can never raise its own trust.

LockerPhycer only issues a ``workspace_id`` claim for the workspace the
account *owns* (``session_claims``), so a JWT caller whose workspace matches
an item's workspace is that workspace's owner. That is the only non-registry
authority recognised, and only for ``deny``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from starlette.requests import Request

from cappo_backend.services.safety import APPROVER_MIN_TRUST, normalize_identity


class ApproverResolutionError(Exception):
    """Raised when no verified approver identity can be established."""

    def __init__(self, code: str, detail: str, *, status_code: int) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ApproverContext:
    """A verified caller, as the quarantine controls see it."""

    approver_id: str
    workspace_id: str | None
    trust: float
    source: str  # "jwt" | "api-key" | "auth-disabled" | "other"
    registered: bool

    @property
    def may_approve(self) -> bool:
        return self.registered and self.trust >= APPROVER_MIN_TRUST

    def owns_workspace(self, workspace_id: str | None) -> bool:
        """True when the caller's session credential is bound to ``workspace_id``.

        Only a JWT carries an owner-bound workspace claim (see module docstring).
        API keys and the auth-disabled sentinel never establish ownership.
        """
        if self.source != "jwt" or not workspace_id or not self.workspace_id:
            return False
        return self.workspace_id == workspace_id

    def may_deny(self, item_workspace_id: str | None) -> bool:
        return self.may_approve or self.owns_workspace(item_workspace_id)


def _scope_str(request: Request, key: str) -> str | None:
    value = request.scope.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_approver(request: Request, settings: Any) -> ApproverContext:
    """Derive the approver strictly from the verified auth context.

    Raises :class:`ApproverResolutionError` (401) when the scope carries no
    verified principal, and (403) when the principal is a mount-holder
    credential, which is a target-side capability credential and never an
    operator.
    """
    principal = _scope_str(request, "auth_principal")
    if principal is None:
        raise ApproverResolutionError(
            "AUTHENTICATION_REQUIRED",
            "No verified principal is bound to this request.",
            status_code=401,
        )

    if principal.startswith("mount-holder:"):
        raise ApproverResolutionError(
            "HOLDER_SCOPE_FORBIDDEN",
            "A mount-holder credential cannot approve or deny quarantined requests.",
            status_code=403,
        )

    workspace_id = _scope_str(request, "auth_workspace")

    if principal.startswith("jwt:"):
        source = "jwt"
        payload = request.scope.get("jwt_payload")
        subject = payload.get("sub") if isinstance(payload, dict) else None
        approver_id = str(subject).strip() if subject and str(subject).strip() else principal
    elif principal.startswith("api-key:"):
        source = "api-key"
        approver_id = principal
    elif principal == "auth-disabled":
        source = "auth-disabled"
        approver_id = principal
    else:
        source = "other"
        approver_id = principal

    registry: dict[str, float] = getattr(settings, "governance_approver_registry", {}) or {}
    key = normalize_identity(approver_id)
    registered = key in registry
    trust = float(registry[key]) if registered else 0.0

    return ApproverContext(
        approver_id=approver_id,
        workspace_id=workspace_id,
        trust=trust,
        source=source,
        registered=registered,
    )


__all__ = ["ApproverContext", "ApproverResolutionError", "resolve_approver"]
