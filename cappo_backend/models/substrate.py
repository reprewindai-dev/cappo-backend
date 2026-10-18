from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from typing_extensions import Annotated


class AttestationType(str, Enum):
    TPM2_0 = "TPM2_0"
    SECURE_BOOT_HWID = "SECURE_BOOT_HWID"
    RAW_HWID = "RAW_HWID"


class SubstrateStatus(str, Enum):
    CANDIDATE_SUBSTRATE = "CANDIDATE_SUBSTRATE"


class HardwareFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    board_serial: str = Field(..., description="Motherboard serial number")
    cpu_id: str = Field(..., description="CPU identifier")
    system_uuid: str = Field(..., description="System UUID")
    mac_addresses: List[str] = Field(..., min_length=1)

    @field_validator("mac_addresses")
    @classmethod
    def validate_macs(cls, macs: List[str]) -> List[str]:
        mac_regex = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
        for mac in macs:
            if not mac_regex.match(mac):
                raise ValueError(f"Invalid MAC address format: {mac}")
        return macs


class HardwareSubstrateRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    substrate_id: Annotated[str, StringConstraints(pattern=r"^sub_hw_[a-f0-9]{16}$")]
    hardware_fingerprint: HardwareFingerprint
    rsa_public_key_pem: str
    attestation_type: AttestationType
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: SubstrateStatus = SubstrateStatus.CANDIDATE_SUBSTRATE


class ExecutionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1)
    project: str = Field(min_length=1)
    resources: Optional[List[str]] = None


class ActionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reads: Optional[List[str]] = None
    writes: Optional[List[str]] = None
    blocked: List[str] = Field(default_factory=list)


class StateBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_digest: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    state_root: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    previous_receipt_hash: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    previous_anchor_id: Optional[str] = None


class AuthorityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous_mount_id: Optional[str] = None
    observed_epoch: Optional[int] = Field(default=None, ge=0)


class Freshness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(min_length=16)
    challenge_nonce: str = Field(min_length=16, max_length=512)
    issued_at: datetime
    expires_at: datetime


class RequestProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alg: str = Field(pattern="^(PS256|EdDSA)$")
    key_id: str = Field(min_length=1)
    canonicalization: str = Field(pattern="^JCS$")
    payload_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    signature: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]


class CapabilityMountRequestSignedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Annotated[str, StringConstraints(pattern=r"^req_[A-Za-z0-9_-]{16,128}$")]
    registration_id: Annotated[str, StringConstraints(pattern=r"^subreg_[A-Za-z0-9_-]{16,128}$")]
    substrate_id: Annotated[str, StringConstraints(pattern=r"^sub_[A-Za-z0-9._:-]{8,128}$")]
    boot_instance_id: str = Field(min_length=16, max_length=256)
    runtime_key_thumbprint: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    execution_profile_digest: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    package_ref: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+@v[0-9]+$")]
    execution_scope: ExecutionScope
    requested_action_scope: ActionScope
    role: str = Field(min_length=1, default="ephemeral_executor")
    execution_id: str = Field(min_length=1, max_length=256)
    requested_ttl_seconds: int = Field(ge=1, le=600)
    state_binding: StateBinding
    authority_context: AuthorityContext
    freshness: Freshness


class SubstrateCapabilityMountRequest(BaseModel):
    """The unified wrapper for capability mount requests signed by a substrate."""
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(pattern="^1.0$")
    type: str = Field(pattern="^veklom.capability_mount_request$")
    signed_payload: CapabilityMountRequestSignedPayload
    proof: RequestProof


class IssuedMount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mount_id: Annotated[str, StringConstraints(pattern=r"^mnt_[A-Za-z0-9_-]{16,128}$")]
    package_ref: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    substrate_id: str = Field(min_length=1)
    boot_instance_id: str = Field(min_length=1)
    runtime_key_thumbprint: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    epoch: int = Field(ge=0)
    issued_at: datetime
    expires_at: datetime
    lifecycle: str = Field(pattern="^(mounted|expired|terminated)$")


class EphemeralMountToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern="^ephemeral_scoped$")
    token_id: str = Field(min_length=1)
    mount_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    ttl_seconds: int = Field(ge=1, le=600)
    single_use: bool = Field(default=True)
    nonce: str = Field(min_length=1)


class AuthorityBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_id: str
    substrate_id: str
    boot_instance_id: str
    runtime_key_thumbprint: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    mount_id: str
    execution_id: str
    epoch: int = Field(ge=0)
    package_digest: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    state_root: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None
    binding_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]


class Anchoring(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(confirmed|pending|not_applicable)$")
    anchor_id: Optional[str] = None


class ResponseProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer: str = Field(pattern="^veklom-cappo$")
    alg: str = Field(pattern="^(EdDSA|PS256)$")
    key_id: str = Field(min_length=1)
    canonicalization: str = Field(pattern="^JCS$")
    payload_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    signature: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]


class SubstrateCapabilityMountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = Field(pattern="^1.0$")
    type: str = Field(pattern="^veklom.capability_mount_response$")
    decision: str = Field(pattern="^(allow|deny)$")
    reason: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    policy_evaluation_id: str = Field(min_length=1)
    
    mount: Optional[IssuedMount] = None
    token: Optional[EphemeralMountToken] = None
    anchoring: Anchoring
    authority_binding: Optional[AuthorityBinding] = None
    response_proof: Optional[ResponseProof] = None

class PGLProducer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producer_id: str = Field(min_length=1)
    producer_role: str = Field(pattern="^(identity_plane|requestor|authority_plane|executor|independent_observer|lifecycle_controller|verifier)$")
    key_thumbprint: Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]
    signature: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$")]

class PGLSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    substrate_id: str = Field(min_length=1)
    boot_instance_id: Optional[str] = None
    registration_id: Optional[str] = None
    mount_id: Optional[str] = None
    execution_id: Optional[str] = None
    package_ref: Optional[str] = None
    authority_epoch: Optional[int] = Field(default=None, ge=0)
    state_root: Optional[Annotated[str, StringConstraints(pattern=r"^sha256:[A-Fa-f0-9]{64}$")]] = None

class PGLUnifiedLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: str = Field(pattern="^veklom.pgl.lifecycle.v1$")
    event_type: str = Field(pattern="^(SUBSTRATE_REGISTERED|BOOT_INSTANCE_OBSERVED|CAPABILITY_REQUESTED|AUTHORITY_ISSUED|LEASE_RENEWED|CONSEQUENCE_ALLOWED|CONSEQUENCE_OBSERVED|FENCE_DENIED|SUBSTRATE_DISCONNECTED|LEASE_EXPIRED|EPOCH_TRANSITION|STALE_AUTHORITY_DENIED|AUTHORITY_REVOKED|SUBSTRATE_DESTROYED)$")
    run_id: str = Field(min_length=8, max_length=256)
    event_id: str = Field(min_length=8, max_length=256)
    occurred_at: datetime
    producer: PGLProducer
    subject: PGLSubject
    details: dict
    prev_event_hash: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Fa-f0-9]{64}$")]] = None
    event_hash: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Fa-f0-9]{64}$")]] = None

