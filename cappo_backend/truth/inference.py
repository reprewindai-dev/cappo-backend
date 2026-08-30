"""Cryptographically fenced inference over certified inbound context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cappo_backend.services.canonical import (
    sha256_json,
    sign_payload_ed25519,
    verify_signature_ed25519,
)
from cappo_backend.truth.models import ClaimState, TruthClaim

RECEIPT_SCHEMA = "veklom.inbound_truth.admissible_context_receipt.v1"


class UncertifiedContextError(Exception):
    """Raised before inference when context certification cannot be proven."""


@dataclass(frozen=True)
class AdmissibleContextReceipt:
    """Signed proof that exact context passed a named inbound-truth policy."""

    claims: tuple[TruthClaim, ...]
    tenant_id: str
    workspace_id: str
    policy_digest: str
    policy_version: str
    issued_at: int
    evaluated_at: int
    receipt_id: str
    nonce: str
    signature: str
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "claims", tuple(self.claims))
        if not self.claims:
            raise UncertifiedContextError("Cannot mint receipt without certified claims.")
        if not all(claim.state == ClaimState.ADMISSIBLE for claim in self.claims):
            raise UncertifiedContextError(
                "Cannot mint receipt: every claim must have an ADMISSIBLE policy decision."
            )
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise UncertifiedContextError("Cannot mint receipt with duplicate claim IDs.")
        if any(claim.tenant_id != self.tenant_id for claim in self.claims):
            raise UncertifiedContextError("Receipt tenant does not match every certified claim.")
        required = {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "policy_digest": self.policy_digest,
            "policy_version": self.policy_version,
            "receipt_id": self.receipt_id,
            "nonce": self.nonce,
        }
        if any(not value.strip() for value in required.values()):
            raise UncertifiedContextError("Receipt identity and policy bindings are required.")
        if self.issued_at > self.evaluated_at:
            raise UncertifiedContextError("Receipt cannot be evaluated before it is issued.")

    def signing_payload(self) -> dict[str, Any]:
        """Return the canonical, mutation-sensitive envelope covered by the signature."""
        claim_bindings = [
            {
                "claim_id": claim.claim_id,
                "digest": sha256_json(claim.model_dump(mode="json")),
            }
            for claim in sorted(self.claims, key=lambda item: item.claim_id)
        ]
        return {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": self.receipt_id,
            "nonce": self.nonce,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "policy_digest": self.policy_digest,
            "policy_version": self.policy_version,
            "issued_at": self.issued_at,
            "evaluated_at": self.evaluated_at,
            "claim_bindings": claim_bindings,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def mint(
        cls,
        claims: list[TruthClaim] | tuple[TruthClaim, ...],
        *,
        tenant_id: str,
        workspace_id: str,
        policy_digest: str,
        policy_version: str,
        issued_at: int,
        evaluated_at: int,
        receipt_id: str,
        nonce: str,
        signing_key: Any,
        evidence_ref: str | None = None,
    ) -> "AdmissibleContextReceipt":
        unsigned = cls(
            claims=tuple(claims),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            policy_digest=policy_digest,
            policy_version=policy_version,
            issued_at=issued_at,
            evaluated_at=evaluated_at,
            receipt_id=receipt_id,
            nonce=nonce,
            signature="pending",
            evidence_ref=evidence_ref,
        )
        return cls(
            **{
                **unsigned.__dict__,
                "signature": sign_payload_ed25519(unsigned.signing_payload(), signing_key),
            }
        )


class InferenceGateway:
    """Mechanically fenced reasoning boundary for exactly bound certified context."""

    def __init__(
        self,
        model_client: Any,
        *,
        trusted_certification_key: Any,
        tenant_id: str,
        workspace_id: str,
        policy_digest: str,
        policy_version: str,
    ) -> None:
        self.model_client = model_client
        self.trusted_certification_key = trusted_certification_key
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.policy_digest = policy_digest
        self.policy_version = policy_version

    def _verify_receipt(self, receipt: AdmissibleContextReceipt) -> None:
        if not isinstance(receipt, AdmissibleContextReceipt):
            raise UncertifiedContextError("Context receipt type is not trusted.")
        if any(claim.state != ClaimState.ADMISSIBLE for claim in receipt.claims):
            raise UncertifiedContextError("Context contains a claim without ADMISSIBLE status.")
        if receipt.tenant_id != self.tenant_id:
            raise UncertifiedContextError("Context receipt tenant binding is invalid.")
        if receipt.workspace_id != self.workspace_id:
            raise UncertifiedContextError("Context receipt workspace binding is invalid.")
        if receipt.policy_digest != self.policy_digest:
            raise UncertifiedContextError("Context receipt policy digest is invalid.")
        if receipt.policy_version != self.policy_version:
            raise UncertifiedContextError("Context receipt policy version is invalid.")
        if not verify_signature_ed25519(
            receipt.signing_payload(), receipt.signature, self.trusted_certification_key
        ):
            raise UncertifiedContextError("Context receipt signature verification failed.")

    def generate_intent(
        self, prompt_template: str, context_receipt: AdmissibleContextReceipt
    ) -> str:
        self._verify_receipt(context_receipt)
        certified_facts = [claim.payload.value for claim in context_receipt.claims]
        safe_prompt = f"{prompt_template}\n\nCERTIFIED CONTEXT:\n{certified_facts}"
        return self.model_client.invoke(safe_prompt)

