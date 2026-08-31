"""Read-only proof projection for completed governed executions.

This module never authorizes or executes. It verifies persisted authorization,
Execution Identity, PGL evidence integrity, and the signed EEE envelope before
returning a proof state to a caller in the same authenticated workspace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cappo_backend.config import Settings, get_settings
from cappo_backend.db.session import get_session
from cappo_backend.models.capability_action_receipt import CapabilityActionReceipt
from cappo_backend.models.consequence_execution import ConsequenceExecutionEvent
from cappo_backend.models.execution_identity import ExecutionIdentity
from cappo_backend.models.governed_run import GovernedRun
from cappo_backend.models.pgl_certificate import PGLCertificate
from cappo_backend.models.pgl_ledger_event import PGLLedgerEvent
from cappo_backend.security.evidence import (
    get_evidence_public_key,
    verify_signed_execution_evidence,
)
from cappo_backend.services.activation_target import (
    ACTIVATION_WRITE_ACTION,
    observe_activation_consequence,
)
from cappo_backend.services.canonical import sha256_json
from cappo_backend.services.eee import EEEBuilder, EEEVerifier, VerificationVerdict
from cappo_backend.services.ei_builder import Ed25519Signer, canonical_body

router = APIRouter(prefix="/v1/executions", tags=["execution-proof"])


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _fail(code: str, detail: str, *, status: int = 409) -> None:
    raise HTTPException(
        status_code=status,
        detail={"error": code, "detail": detail, "fail_closed": True},
    )


def _run_for_workspace(db: Session, execution_id: str, workspace_id: str) -> GovernedRun:
    run = db.execute(
        select(GovernedRun).where(
            GovernedRun.run_id == execution_id,
            GovernedRun.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if run is None:
        _fail(
            "EXECUTION_NOT_FOUND",
            "No governed execution exists in this workspace.",
            status=404,
        )
    return run


def _authorization_receipt(db: Session, execution_id: str) -> CapabilityActionReceipt:
    receipts = db.execute(
        select(CapabilityActionReceipt)
        .where(
            CapabilityActionReceipt.execution_id == execution_id,
            CapabilityActionReceipt.decision == "allow",
        )
        .order_by(CapabilityActionReceipt.actioned_at.asc())
    ).scalars().all()
    if len(receipts) != 1:
        _fail(
            "AUTHORIZATION_RECEIPT_CARDINALITY_INVALID",
            "A single-use execution must have exactly one persisted ALLOW receipt.",
        )
    receipt = receipts[0]
    canonical = {
        "execution_id": receipt.execution_id,
        "mount_id": receipt.mount_id,
        "token_id": receipt.token_id,
        "principal": receipt.principal,
        "caller_spiffe_id": receipt.caller_spiffe_id,
        "executor_spiffe_id": receipt.executor_spiffe_id,
        "eei_id": receipt.eei_id,
        "profile_id": receipt.profile_id,
        "lease_id": receipt.lease_id,
        "operator_id": receipt.operator_id,
        "caller_cert_sha256": receipt.caller_cert_sha256,
        "capability_id": receipt.capability_id,
        "biscuit_token_sha256": receipt.biscuit_token_sha256,
        "action": receipt.action,
        "resource": receipt.resource or "*",
        "policy_version": receipt.policy_version,
        "decision": receipt.decision,
        "reason": receipt.reason,
        "timestamp": _iso_utc(receipt.actioned_at),
        "actioned_at": _iso_utc(receipt.actioned_at),
        "result_hash": None,
        "pgl_anchor_id": receipt.pgl_anchor_id,
    }
    if sha256_json(canonical) != receipt.content_hash:
        _fail(
            "AUTHORIZATION_RECEIPT_HASH_MISMATCH",
            "Authorization receipt integrity check failed.",
        )
    if not receipt.signed_receipt_cose:
        _fail(
            "AUTHORIZATION_RECEIPT_SIGNATURE_MISSING",
            "Authorization receipt is not signed.",
        )
    try:
        signed_payload = verify_signed_execution_evidence(
            receipt.signed_receipt_cose,
            get_evidence_public_key(),
        )
    except ValueError as exc:
        _fail(
            "AUTHORIZATION_RECEIPT_SIGNATURE_INVALID",
            f"Authorization receipt signature verification failed: {exc}",
        )
    if signed_payload != canonical:
        _fail(
            "AUTHORIZATION_RECEIPT_SIGNED_PAYLOAD_MISMATCH",
            "Signed authorization receipt payload does not match the persisted receipt.",
        )
    return receipt


def _execution_identity(
    db: Session,
    run: GovernedRun,
    execution_id: str,
    settings: Settings,
) -> ExecutionIdentity:
    identity = db.get(ExecutionIdentity, execution_id)
    if identity is None or identity.run_id != run.run_id:
        _fail(
            "EXECUTION_IDENTITY_MISSING",
            "Persisted Execution Identity is missing or misbound.",
        )
    body = identity.identity_json or {}
    if body != (run.execution_identity or {}):
        _fail(
            "EXECUTION_IDENTITY_PROJECTION_MISMATCH",
            "Run and EI projections do not match.",
        )
    if body.get("execution_id") != execution_id and body.get("ei_id") != execution_id:
        _fail(
            "EXECUTION_IDENTITY_ID_MISMATCH",
            "Execution Identity does not bind this execution id.",
        )
    if body.get("hash") != sha256_json(canonical_body(body)):
        _fail(
            "EXECUTION_IDENTITY_HASH_MISMATCH",
            "Execution Identity hash is invalid.",
        )
    signature = body.get("signature")
    valid_signature = isinstance(signature, str) and Ed25519Signer(
        settings.ei_signing_key
    ).verify(canonical_body(body), signature)
    if not valid_signature:
        _fail(
            "EXECUTION_IDENTITY_SIGNATURE_INVALID",
            "Execution Identity signature is invalid.",
        )
    return identity


def _verify_event_chain(db: Session, event: PGLLedgerEvent) -> list[str]:
    unresolved: list[str] = []
    current = event
    visited: set[str] = set()
    while True:
        if current.event_hash in visited:
            _fail("PGL_CHAIN_CYCLE", "PGL evidence chain contains a cycle.")
        visited.add(current.event_hash)
        expected = sha256_json(
            {**(current.payload or {}), "previous_event_hash": current.previous_event_hash}
        )
        if expected != current.event_hash:
            _fail(
                "PGL_EVENT_HASH_MISMATCH",
                "PGL event hash does not match its persisted payload.",
            )
        previous_hash = current.previous_event_hash
        if not previous_hash:
            return unresolved
        previous = db.execute(
            select(PGLLedgerEvent).where(PGLLedgerEvent.event_hash == previous_hash)
        ).scalar_one_or_none()
        if previous is None:
            unresolved.append("PGL_PREVIOUS_EVENT_UNRESOLVED")
            return unresolved
        current = previous


def _verify_eee(
    seal: dict[str, Any],
    execution_id: str,
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    envelope = seal.get("eee")
    if not isinstance(envelope, dict):
        _fail("EEE_MISSING", "Persisted evidence seal contains no EEE envelope.")
    if envelope.get("execution_id") != execution_id:
        _fail("EEE_EXECUTION_ID_MISMATCH", "EEE is bound to another execution id.")
    if seal.get("evidence_id") != envelope.get("envelope_hash"):
        _fail(
            "EEE_SEAL_ID_MISMATCH",
            "PGL seal does not commit the EEE envelope hash.",
        )

    builder = EEEBuilder(
        signing_key=settings.ei_signing_key,
        issuer=settings.capability_beacon_issuer,
        kid=settings.capability_beacon_kid,
    )
    verification = EEEVerifier(
        {settings.capability_beacon_kid: builder.public_key_bytes}
    ).verify(envelope)
    if verification.verdict not in {
        VerificationVerdict.VALID,
        VerificationVerdict.VALID_WITH_UNRESOLVED_REFS,
    }:
        _fail(
            "EEE_VERIFICATION_FAILED",
            f"EEE verification failed: {verification.reasons}",
        )
    unresolved = (
        list(verification.reasons)
        if verification.verdict is VerificationVerdict.VALID_WITH_UNRESOLVED_REFS
        else []
    )
    return envelope, unresolved


def _pgl_evidence(
    db: Session,
    run: GovernedRun,
    execution_id: str,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    pgl_identity = run.pgl_identity or {}
    event_id = pgl_identity.get("capi_evidence_event_id")
    if not isinstance(event_id, str) or not event_id:
        _fail(
            "PGL_EVIDENCE_EVENT_MISSING",
            "Run has no acknowledged PGL evidence event.",
        )

    event = db.get(PGLLedgerEvent, event_id)
    if event is not None:
        if event.event_type != "capi_evidence_sealed":
            _fail(
                "PGL_EVENT_TYPE_INVALID",
                "Execution evidence is bound to the wrong PGL event type.",
            )
        certificate = db.get(PGLCertificate, event.certificate_id)
        if certificate is None or certificate.persisted is not True:
            _fail(
                "PGL_CERTIFICATE_NOT_PERSISTED",
                "PGL certificate is missing or not durable.",
            )
        if certificate.run_id != run.run_id or certificate.workspace_id != run.workspace_id:
            _fail(
                "PGL_CERTIFICATE_BINDING_MISMATCH",
                "PGL certificate is bound to another execution.",
            )
        unresolved = _verify_event_chain(db, event)
        seal = (event.payload or {}).get("evidence_seal")
        if not isinstance(seal, dict):
            _fail("EEE_SEAL_MISSING", "PGL event contains no evidence seal.")
        envelope, eee_unresolved = _verify_eee(seal, execution_id, settings)
        unresolved.extend(eee_unresolved)
        return (
            {
                "event_id": event.event_id,
                "certificate_id": event.certificate_id,
                "event_hash": event.event_hash,
                "previous_event_hash": event.previous_event_hash,
                "persisted": True,
                "external": False,
                "created_at": _iso_utc(event.created_at),
            },
            envelope,
            list(dict.fromkeys(unresolved)),
        )

    # External PGL adapters acknowledge a durable event id but do not mirror the
    # canonical event row into CAPPO's local database. The signed evidence
    # projection is retained only after that acknowledgement. Verify the signed
    # projection and expose the external references as unresolved rather than
    # inventing an event hash CAPPO cannot independently recompute.
    seal = pgl_identity.get("capi_evidence_seal")
    seal_hash = pgl_identity.get("capi_evidence_seal_hash")
    if not isinstance(seal, dict) or not isinstance(seal_hash, str) or not seal_hash:
        _fail(
            "PGL_EXTERNAL_EVIDENCE_PROJECTION_MISSING",
            "External PGL acknowledgement exists but CAPPO has no signed evidence projection.",
        )
    if seal.get("seal_hash") != seal_hash:
        _fail(
            "PGL_EXTERNAL_EVIDENCE_PROJECTION_MISMATCH",
            "Stored external evidence projection does not match its committed seal hash.",
        )
    if pgl_identity.get("persisted") is not True:
        _fail(
            "PGL_CERTIFICATE_NOT_PERSISTED",
            "External PGL certificate was not acknowledged as persistent.",
        )
    certificate_id = pgl_identity.get("post_execution_certificate_id")
    if not isinstance(certificate_id, str) or not certificate_id:
        _fail(
            "PGL_POST_CERTIFICATE_MISSING",
            "Completed execution has no acknowledged post-execution certificate.",
        )
    envelope, unresolved = _verify_eee(seal, execution_id, settings)
    unresolved.extend(
        ["PGL_EVENT_EXTERNAL_UNRESOLVED", "PGL_CERTIFICATE_EXTERNAL_UNRESOLVED"]
    )
    return (
        {
            "event_id": event_id,
            "certificate_id": certificate_id,
            "event_hash": None,
            "previous_event_hash": None,
            "persisted": True,
            "external": True,
            "created_at": _iso_utc(run.updated_at),
        },
        envelope,
        list(dict.fromkeys(unresolved)),
    )


def execution_evidence_projection(
    db: Session,
    execution_id: str,
    workspace_id: str,
    settings: Settings,
) -> dict[str, Any]:
    run = _run_for_workspace(db, execution_id, workspace_id)
    receipt = _authorization_receipt(db, execution_id)
    identity = _execution_identity(db, run, execution_id, settings)
    pgl, envelope, unresolved = _pgl_evidence(db, run, execution_id, settings)

    if envelope.get("status") != "completed" or run.result_payload is None:
        _fail(
            "EXECUTION_NOT_PROVEN_COMPLETE",
            "Persisted evidence does not prove a completed execution.",
        )
    if identity.pgl_post_certificate_id != pgl["certificate_id"]:
        _fail(
            "PGL_POST_CERTIFICATE_MISMATCH",
            "EI post-certificate and evidence event disagree.",
        )

    return {
        "execution_id": execution_id,
        "run_id": run.run_id,
        "proof_state": "verified_with_unresolved_refs" if unresolved else "verified",
        "verification_reasons": unresolved,
        "authorization": {
            "receipt_id": receipt.receipt_id,
            "mount_id": receipt.mount_id,
            "token_id": receipt.token_id,
            "action": receipt.action,
            "decision": receipt.decision,
            "content_hash": receipt.content_hash,
            "pgl_anchor_id": receipt.pgl_anchor_id,
            "authorized_at": _iso_utc(receipt.actioned_at),
        },
        "execution_identity": {
            "execution_id": execution_id,
            "authority_bundle_hash": identity.authority_bundle_hash,
            "policy_hash": identity.policy_hash,
            "pgl_pre_certificate_id": identity.pgl_certificate_id,
            "pgl_post_certificate_id": identity.pgl_post_certificate_id,
        },
        "eee": envelope,
        "pgl": pgl,
    }


def execution_measurements_projection(
    db: Session,
    execution_id: str,
    workspace_id: str,
    settings: Settings,
) -> dict[str, Any]:
    evidence = execution_evidence_projection(db, execution_id, workspace_id, settings)
    run = _run_for_workspace(db, execution_id, workspace_id)
    events = db.execute(
        select(ConsequenceExecutionEvent)
        .where(ConsequenceExecutionEvent.execution_id == execution_id)
        .order_by(
            ConsequenceExecutionEvent.operation_id.asc(),
            ConsequenceExecutionEvent.version.asc(),
        )
    ).scalars().all()

    operations: dict[str, list[ConsequenceExecutionEvent]] = {}
    for event in events:
        operations.setdefault(event.operation_id, []).append(event)
    terminal_success = {"succeeded", "reconciled_succeeded"}
    terminal_failure = {"failed", "reconciled_failed"}
    successful = sum(
        1 for stream in operations.values() if stream[-1].state in terminal_success
    )
    failed = sum(
        1 for stream in operations.values() if stream[-1].state in terminal_failure
    )
    outcome_unknown = sum(
        1 for stream in operations.values() if stream[-1].state == "outcome_unknown"
    )

    result = run.result_payload or {}
    target_observation: dict[str, Any] | None = None
    if evidence["authorization"]["action"] == ACTIVATION_WRITE_ACTION:
        observation = observe_activation_consequence(
            db,
            execution_id=execution_id,
            workspace_id=workspace_id,
        ).as_dict()
        if observation["consequence_count"] != 1:
            _fail(
                "ACTIVATION_TARGET_CONSEQUENCE_COUNT_INVALID",
                "A successful Activation execution must have exactly one durable target consequence.",
            )
        if observation["mount_id"] != evidence["authorization"]["mount_id"]:
            _fail(
                "ACTIVATION_TARGET_MOUNT_MISMATCH",
                "Target consequence is bound to another capability mount.",
            )
        if observation["receipt_id"] != evidence["authorization"]["receipt_id"]:
            _fail(
                "ACTIVATION_TARGET_RECEIPT_MISMATCH",
                "Target consequence is bound to another authorization receipt.",
            )
        persisted_result = result.get("activation_target")
        if not isinstance(persisted_result, dict):
            _fail(
                "ACTIVATION_TARGET_RESULT_MISSING",
                "Execution result contains no Activation target commitment.",
            )
        if (
            persisted_result.get("consequence_id") != observation["consequence_id"]
            or persisted_result.get("content_hash") != observation["content_hash"]
        ):
            _fail(
                "ACTIVATION_TARGET_RESULT_MISMATCH",
                "Persisted execution result disagrees with the independently observed target row.",
            )
        if len(operations) != 1:
            _fail(
                "ACTIVATION_LIFECYCLE_OPERATION_COUNT_INVALID",
                "Activation must contain exactly one consequence operation stream.",
            )
        stream = next(iter(operations.values()))
        if [event.state for event in stream] != ["authorized", "started", "succeeded"]:
            _fail(
                "ACTIVATION_LIFECYCLE_SEQUENCE_INVALID",
                "Activation lifecycle must be exactly AUTHORIZED -> STARTED -> SUCCEEDED.",
            )
        if any(
            event.receipt_id != evidence["authorization"]["receipt_id"]
            or event.mount_id != evidence["authorization"]["mount_id"]
            or event.action != ACTIVATION_WRITE_ACTION
            for event in stream
        ):
            _fail(
                "ACTIVATION_LIFECYCLE_AUTHORITY_BINDING_MISMATCH",
                "Activation lifecycle is not bound to the same mount, receipt, and action as the target consequence.",
            )
        terminal = stream[-1]
        if (
            terminal.completion_proof_type != "durable_target_row"
            or terminal.completion_proof_ref != observation["content_hash"]
        ):
            _fail(
                "ACTIVATION_LIFECYCLE_TARGET_PROOF_MISMATCH",
                "Activation SUCCEEDED state is not proven by the independently observed durable target row.",
            )
        target_observation = observation

    elapsed_ms = max(0.0, (run.updated_at - run.created_at).total_seconds() * 1000)
    return {
        "execution_id": execution_id,
        "run_id": run.run_id,
        "proof_state": evidence["proof_state"],
        "run_state": run.state,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "tokens": result.get("tokens"),
        "cached": result.get("cached"),
        "cache_tier": result.get("cache_tier"),
        "runtime_elapsed_ms": round(elapsed_ms, 3),
        "started_at": _iso_utc(run.created_at),
        "ended_at": _iso_utc(run.updated_at),
        "authorization_count": 1,
        "consequence": {
            "operation_count": len(operations),
            "successful_count": successful,
            "failed_count": failed,
            "outcome_unknown_count": outcome_unknown,
            "events": [
                {
                    "event_id": event.event_id,
                    "operation_id": event.operation_id,
                    "state": event.state,
                    "version": event.version,
                    "action": event.action,
                    "resource": event.resource,
                    "receipt_id": event.receipt_id,
                    "completion_proof_type": event.completion_proof_type,
                    "completion_proof_ref": event.completion_proof_ref,
                    "created_at": _iso_utc(event.created_at),
                }
                for event in events
            ],
        },
        "pgl_event_id": evidence["pgl"]["event_id"],
        "pgl_event_hash": evidence["pgl"]["event_hash"],
        "eee_envelope_hash": evidence["eee"]["envelope_hash"],
        "target_observation": target_observation,
    }


@router.get("/{execution_id}/target-observation")
def activation_target_observation(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return observe_activation_consequence(
        db,
        execution_id=execution_id,
        workspace_id=str(request.scope["auth_workspace"]),
    ).as_dict()


@router.get("/{execution_id}/evidence")
def execution_evidence(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return execution_evidence_projection(
        db,
        execution_id,
        str(request.scope["auth_workspace"]),
        settings,
    )


@router.get("/{execution_id}/measurements")
def execution_measurements(
    execution_id: str,
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return execution_measurements_projection(
        db,
        execution_id,
        str(request.scope["auth_workspace"]),
        settings,
    )


__all__ = [
    "execution_evidence_projection",
    "execution_measurements_projection",
    "router",
]
