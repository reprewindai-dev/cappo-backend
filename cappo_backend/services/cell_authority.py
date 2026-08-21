"""CAPPO issuance of one-time Lockerphycer governed-cell authority.

CAPPO remains the sole consequence authority. This module attenuates a governed
run into an exact, short-lived, audience-bound envelope that Lockerphycer can
verify independently before spawning a cell or brokering an external effect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.services.canonical import (
    get_ed25519_private_key,
    sign_payload_ed25519,
)


class CellAuthorityError(RuntimeError):
    """CAPPO cannot construct a valid bounded cell authority."""


class GitHubFileUpdateIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="github", pattern="^github$")
    operation: str = Field(default="github.file.update", pattern=r"^github\.file\.update$")
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    branch: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=4096)
    expected_blob_sha: str = Field(min_length=40, max_length=64)
    content_b64: str = Field(min_length=1)
    commit_message: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def reject_parent_traversal(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("GitHub path must be repository-relative without parent traversal")
        return value


class CellResourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpus: float = Field(default=0.5, gt=0, le=4)
    memory_mb: int = Field(default=128, ge=32, le=2048)
    pids: int = Field(default=32, ge=8, le=256)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    tmpfs_mb: int = Field(default=64, ge=16, le=512)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def semantic_intent_digest(intent: GitHubFileUpdateIntent) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(intent.model_dump(mode="json"))).hexdigest()


def cell_authority_public_key_b64url(signing_key: str) -> str:
    """Return the raw public Ed25519 key Lockerphycer must pin for this signer."""
    from cryptography.hazmat.primitives import serialization

    public = get_ed25519_private_key(signing_key).public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


class CellAuthorityBuilder:
    """Attenuate one already-governed CAPPO execution into a cell authority."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.key_id = os.environ.get("CAPPO_CELL_AUTHORITY_KID", "").strip()
        self.runtime_instance = os.environ.get("CAPPO_LOCKERPHYCER_CELL_INSTANCE", "").strip()
        self.ttl_seconds = int(os.environ.get("CAPPO_CELL_AUTHORITY_TTL_SECONDS", "30"))
        if not self.key_id:
            raise CellAuthorityError("CAPPO_CELL_AUTHORITY_KID is required when governed cells are enabled")
        if not self.runtime_instance:
            raise CellAuthorityError("CAPPO_LOCKERPHYCER_CELL_INSTANCE is required when governed cells are enabled")
        if not 1 <= self.ttl_seconds <= 300:
            raise CellAuthorityError("CAPPO_CELL_AUTHORITY_TTL_SECONDS must be between 1 and 300")

    def build_from_execution_request(self, request: dict[str, Any], db: Any) -> dict[str, Any]:
        """Build from the persisted EI that CAPPO already minted before execution.

        The executor-facing request contains only a small routing envelope. We do
        not trust caller fields to recreate authority. Instead, resolve the
        persisted signed ExecutionIdentity by execution_id and use that identity's
        runtime ownership, subject, scope, expiry, and policy binding.
        """
        authority = request.get("authority_envelope")
        if not isinstance(authority, dict):
            raise CellAuthorityError("CAPPO executor request is missing its authority envelope")
        execution_id = authority.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise CellAuthorityError("CAPPO executor request is missing execution_id")

        record = db.get(ExecutionIdentity, execution_id)
        if record is None or not isinstance(record.identity_json, dict):
            raise CellAuthorityError("persisted CAPPO execution identity was not found")
        identity = record.identity_json
        if identity.get("execution_id") != execution_id:
            raise CellAuthorityError("persisted CAPPO execution identity does not match execution_id")

        canonical_workspace = str(getattr(record, "tenant_id", "") or request.get("workspace_id") or "")
        if not canonical_workspace:
            raise CellAuthorityError("canonical workspace is missing from persisted CAPPO identity")
        if request.get("workspace_id") != canonical_workspace:
            raise CellAuthorityError("executor workspace does not match persisted CAPPO identity")

        run = SimpleNamespace(
            request_payload=request,
            execution_identity=identity,
            run_id=execution_id,
            workspace_id=canonical_workspace,
            approved_budget_cents=int(request.get("budget_approved_cents") or 0),
        )
        return self.build(run)

    def build(self, run: Any) -> dict[str, Any]:
        request = run.request_payload or {}
        identity = run.execution_identity or {}
        if not identity:
            raise CellAuthorityError("execution identity is required before cell authority")

        action = request.get("action")
        if action != "github.file.update":
            raise CellAuthorityError("P0 governed cell currently supports github.file.update only")

        raw_effect = request.get("effect")
        if not isinstance(raw_effect, dict):
            raise CellAuthorityError("github.file.update requires an exact effect object")
        intent = GitHubFileUpdateIntent.model_validate(raw_effect)

        scope = identity.get("scope")
        allowed = scope.get("allowed_provider_set") if isinstance(scope, dict) else None
        if not isinstance(allowed, list) or "github" not in allowed:
            raise CellAuthorityError("CAPPO execution identity does not authorize provider github")

        ownership = identity.get("runtime_ownership")
        if not isinstance(ownership, dict):
            raise CellAuthorityError("runtime ownership is required before cell authority")

        raw_limits = request.get("cell_limits") or {}
        if not isinstance(raw_limits, dict):
            raise CellAuthorityError("cell_limits must be an object")
        limits = CellResourceLimits.model_validate(raw_limits)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.ttl_seconds)
        identity_exp = identity.get("expires_at")
        if isinstance(identity_exp, str) and identity_exp:
            parent_exp = datetime.fromisoformat(identity_exp)
            if parent_exp.tzinfo is None:
                parent_exp = parent_exp.replace(tzinfo=timezone.utc)
            expires = min(expires, parent_exp)
        if expires <= now:
            raise CellAuthorityError("parent execution identity expires before cell authority can be issued")

        execution_id = str(identity.get("execution_id") or run.run_id)
        envelope = {
            "execution_id": execution_id,
            "path_id": str(ownership.get("path_id") or run.run_id),
            "request_id": str(request.get("request_id") or run.run_id),
            "idempotency_key": str(request.get("idempotency_key") or run.run_id),
            "grant_id": f"cell-{uuid.uuid4()}",
            "subject_id": str(identity.get("subject") or identity.get("issuer") or "unknown"),
            "delegation_id": request.get("delegation_id"),
            # Authenticated workspace is canonicalized before RunOrchestrator.
            "tenant_id": str(run.workspace_id),
            "workspace_id": str(run.workspace_id),
            "capability_id": intent.operation,
            "semantic_intent_digest": semantic_intent_digest(intent),
            "resource_constraints": limits.model_dump(mode="json"),
            "authority_epoch": int(ownership.get("authority_epoch") or 0),
            "assignment_id": str(ownership.get("assignment_id") or ""),
            "runtime_kind": "lockerphycer-cell",
            "runtime_instance": self.runtime_instance,
            "policy_digest": str(identity.get("policy_hash") or identity.get("seked_attestation_hash") or ""),
            # Attenuate to the one provider needed for this effect.
            "allowed_provider_set": ["github"],
            "budget_ceiling": int(run.approved_budget_cents or 0),
            "evidence_profile": "pgl-required",
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "nonce": uuid.uuid4().hex,
        }
        if not envelope["assignment_id"] or not envelope["policy_digest"]:
            raise CellAuthorityError("cell authority is missing assignment or policy binding")

        signature = sign_payload_ed25519(envelope, self.settings.ei_signing_key)
        return {
            "envelope": envelope,
            "proof": {
                "algorithm": "Ed25519",
                "key_id": self.key_id,
                "signature_b64url": signature,
            },
        }
