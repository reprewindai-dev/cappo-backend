"""EEE-Core v0.1.0 envelope construction and offline verification.

EEE is durable evidence about an already-gated execution.  It is never an
authorization credential and this module deliberately has no execution or
routing entry point.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cappo_backend.services.canonical import get_ed25519_private_key, sha256_json

_SUPPORTED_VERSION = "0.1.0"
_ALLOWED_HASH_ALGORITHMS = {"SHA-256": hashlib.sha256, "SHA-384": hashlib.sha384}
_INTEGRITY_FIELDS = {"envelope_hash", "previous_envelope_hash", "signatures"}
_REQUIRED_FIELDS = {
    "eee_version",
    "execution_id",
    "idempotency_key",
    "issuer",
    "enforcer",
    "participant_identity",
    "principal",
    "capability_id",
    "capability_hash",
    "capability_attenuation",
    "runtime_lineage",
    "authority_chain",
    "authority_window",
    "revocation_check",
    "policy_bundle_id",
    "policy_hash",
    "policy_decisions",
    "enforcement_mode",
    "input_commitment",
    "allowed_effects",
    "actual_effects",
    "tool_actions",
    "budget",
    "started_at",
    "ended_at",
    "output_commitment",
    "status",
    "violations",
    "validators",
    "envelope_hash",
    "signatures",
    "timestamps",
}


class VerificationVerdict(StrEnum):
    VALID = "VALID"
    VALID_WITH_UNRESOLVED_REFS = "VALID_WITH_UNRESOLVED_REFS"
    INVALID = "INVALID"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"


@dataclass(frozen=True)
class VerificationReport:
    verdict: VerificationVerdict
    reasons: list[str]


class EEEBuilder:
    """Build one signed, immutable EEE record from a CAPPO/PGL event."""

    def __init__(self, *, signing_key: str, issuer: str, kid: str) -> None:
        self._private_key = get_ed25519_private_key(signing_key)
        self._issuer = issuer
        self._kid = kid

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes_raw()

    def build(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        envelope = dict(fields)
        if envelope.get("issuer") != self._issuer:
            raise ValueError("EEE issuer must match the configured signing issuer")
        if envelope.get("eee_version") != _SUPPORTED_VERSION:
            raise ValueError("EEEBuilder only produces EEE v0.1.0")
        envelope["hash_alg"] = envelope.get("hash_alg", "SHA-256")
        algorithm = _algorithm(envelope["hash_alg"])
        envelope["envelope_hash"] = _root(envelope, algorithm)
        signature = self._private_key.sign(envelope["envelope_hash"].encode("ascii"))
        envelope["signatures"] = [
            {
                "signer": f"{self._issuer}#{self._kid}",
                "kid": self._kid,
                "scheme": "Ed25519",
                "value": _b64url(signature),
                "signed_at": envelope["timestamps"]["issued_at"],
            }
        ]
        return envelope


class EEEVerifier:
    """Offline EEE verifier; callers provide already-resolved issuer keys."""

    def __init__(self, public_keys: Mapping[str, bytes]) -> None:
        self._public_keys = dict(public_keys)

    def verify(self, envelope: Mapping[str, Any]) -> VerificationReport:
        reasons: list[str] = []
        if not isinstance(envelope, Mapping):
            return VerificationReport(VerificationVerdict.INVALID, ["ENVELOPE_NOT_OBJECT"])
        missing = sorted(field for field in _REQUIRED_FIELDS if field not in envelope)
        if missing:
            return VerificationReport(VerificationVerdict.INVALID, [f"MISSING:{field}" for field in missing])
        if envelope.get("eee_version") != _SUPPORTED_VERSION:
            return VerificationReport(VerificationVerdict.UNSUPPORTED_VERSION, ["UNSUPPORTED_VERSION"])
        try:
            algorithm = _algorithm(str(envelope.get("hash_alg", "SHA-256")))
        except ValueError:
            return VerificationReport(VerificationVerdict.INVALID, ["HASH_ALGORITHM_NOT_ALLOWED"])
        try:
            expected_root = _root(envelope, algorithm)
        except (TypeError, ValueError, rfc8785.CanonicalizationError):
            return VerificationReport(VerificationVerdict.INVALID, ["CANONICALIZATION_FAILED"])
        if envelope.get("envelope_hash") != expected_root:
            return VerificationReport(VerificationVerdict.INVALID, ["ENVELOPE_HASH_MISMATCH"])
        if not self._verify_issuer_signature(envelope):
            return VerificationReport(VerificationVerdict.INVALID, ["ISSUER_SIGNATURE_INVALID"])
        invalid, unresolved = _semantic_reasons(envelope)
        if invalid:
            return VerificationReport(VerificationVerdict.INVALID, invalid)
        if unresolved:
            return VerificationReport(VerificationVerdict.VALID_WITH_UNRESOLVED_REFS, unresolved)
        return VerificationReport(VerificationVerdict.VALID, reasons)

    def _verify_issuer_signature(self, envelope: Mapping[str, Any]) -> bool:
        signatures = envelope.get("signatures")
        if not isinstance(signatures, list):
            return False
        issuer = envelope.get("issuer")
        for signature in signatures:
            if not isinstance(signature, Mapping):
                continue
            if signature.get("scheme") != "Ed25519":
                continue
            kid = signature.get("kid")
            signer = signature.get("signer")
            if not isinstance(kid, str) or not isinstance(signer, str):
                continue
            if signer != f"{issuer}#{kid}" or kid not in self._public_keys:
                continue
            try:
                key = Ed25519PublicKey.from_public_bytes(self._public_keys[kid])
                key.verify(_b64url_decode(str(signature.get("value", ""))), str(envelope["envelope_hash"]).encode("ascii"))
                return True
            except (ValueError, InvalidSignature):
                continue
        return False


def build_terminal_eee(
    run: Any,
    *,
    result: Mapping[str, Any] | None,
    builder: EEEBuilder,
) -> dict[str, Any]:
    """Build the one signed EEE-Core record for an already terminal CAPPO run.

    This function is deliberately evidence-only: it consumes persisted run
    state and cannot authorize, route, execute, or settle anything. Unknown
    execution effects and revocation state are represented conservatively
    rather than invented.
    """
    request = _mapping(getattr(run, "request_payload", None))
    scope = _mapping(getattr(run, "scope", None))
    hashes = _mapping(getattr(run, "hashes", None))
    identity = _mapping(getattr(run, "execution_identity", None))
    now = _timestamp_value(getattr(run, "updated_at", None) or datetime.now(UTC))
    started = _timestamp_value(getattr(run, "created_at", None) or datetime.now(UTC))
    directive = str(getattr(run, "governance_decision", "") or "").upper()
    denied = directive in {"DENY", "DENIED"}
    execution_id = str(getattr(run, "run_id"))
    action = request.get("action") if isinstance(request.get("action"), str) else None
    tools = scope.get("tools")
    capability_id = action or (tools[0] if isinstance(tools, list) and tools and isinstance(tools[0], str) else "unknown")
    allowed_effects = scope.get("allowed_effects")
    if not _string_list(allowed_effects):
        allowed_effects = []
    actor = request.get("agent_id") or request.get("pgl_id") or "unknown"
    principal = request.get("tenant_id") or getattr(run, "tenant_id", "unknown")
    issued = _canonical_timestamp(identity.get("issued_at"), fallback=started)
    expires = _canonical_timestamp(identity.get("expires_at"), fallback=now)
    result_map = dict(result or {})
    status = "denied" if denied else "completed" if result is not None else "error"
    budget_granted = int(getattr(run, "approved_budget_cents", 0) or 0)

    fields: dict[str, Any] = {
        "eee_version": _SUPPORTED_VERSION,
        "execution_id": execution_id,
        "idempotency_key": request.get("idempotency_key") or execution_id,
        "issuer": builder._issuer,
        "enforcer": {"name": "cappo", "version": "0.1.0", "build_hash": "unresolved"},
        "participant_identity": {"scheme": "veklom-machine", "identifier": str(actor)},
        "principal": {"scheme": "veklom-tenant", "identifier": str(principal)},
        "capability_id": capability_id,
        "capability_hash": hashes.get("tool_manifest_hash") or sha256_json(scope),
        "capability_attenuation": {
            "resource_allowlist": allowed_effects,
            "argument_constraints": {},
            "spend_ceiling": str(budget_granted),
            "rate_limits": {},
            "delegation_depth": int(getattr(run, "delegation_depth", 0) or 0),
            "delegation_depth_max": int(getattr(run, "delegation_depth", 0) or 0),
        },
        "runtime_lineage": {
            "model": result_map.get("model") or "unresolved",
            "model_version": "unresolved",
            "framework": "cappo",
            "framework_version": "0.1.0",
            "config_hash": sha256_json(hashes),
        },
        "authority_chain": [],
        "authority_window": {"not_before": issued, "not_after": expires},
        "revocation_check": {"method": "none", "checked_at": now, "result": "unresolved"},
        "policy_bundle_id": "cappo-governance-decision",
        "policy_hash": hashes.get("constitution_hash") or sha256_json({"directive": directive}),
        "policy_decisions": [{
            "gate": "cappo-governance",
            "rule_id": directive or "decision-missing",
            "decision": "deny" if denied else "allow" if result is not None else "deny",
            "evaluated_at": now,
            "latency_ms": 0,
            "reason_code": directive or "DECISION_UNRESOLVED",
        }],
        "enforcement_mode": "fail-closed",
        "input_commitment": hashes.get("input_hash") or sha256_json(request),
        "allowed_effects": allowed_effects,
        "actual_effects": [],
        "tool_actions": [],
        "budget": {"granted": {"cost_cents": budget_granted}, "consumed": {"cost_cents": 0}},
        "started_at": started,
        "ended_at": now,
        "output_commitment": sha256_json(result_map if result is not None else {"status": status}),
        "status": status,
        "violations": [],
        "validators": [],
        "timestamps": {"issued_at": now},
    }
    return builder.build(fields)


def _algorithm(name: str):
    try:
        return _ALLOWED_HASH_ALGORITHMS[name]
    except KeyError as exc:
        raise ValueError("unsupported EEE hash algorithm") from exc


def _root(envelope: Mapping[str, Any], algorithm: Any) -> str:
    body = {key: value for key, value in envelope.items() if key not in _INTEGRITY_FIELDS}
    return f"{_algorithm_name(algorithm)}:{algorithm(rfc8785.dumps(body)).hexdigest()}"


def _algorithm_name(algorithm: Any) -> str:
    return "sha256" if algorithm is hashlib.sha256 else "sha384"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _semantic_reasons(envelope: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    invalid: list[str] = []
    unresolved: list[str] = []
    try:
        started = _timestamp(envelope["started_at"])
        ended = _timestamp(envelope["ended_at"])
        window = envelope["authority_window"]
        not_before = _timestamp(window["not_before"])
        not_after = _timestamp(window["not_after"])
    except (KeyError, TypeError, ValueError):
        return ["TIMESTAMP_INVALID"], unresolved
    tolerance = timedelta(milliseconds=250)
    if ended < started:
        invalid.append("EXECUTION_TIME_INVALID")
    if started < not_before - tolerance or started > not_after + tolerance:
        invalid.append("AUTHORITY_WINDOW_VIOLATION")
    for link in envelope["authority_chain"]:
        if not isinstance(link, Mapping):
            invalid.append("AUTHORITY_CHAIN_INVALID")
            continue
        try:
            if _timestamp(link["expires_at"]) + tolerance < started:
                invalid.append("AUTHORITY_CHAIN_EXPIRED")
        except (KeyError, TypeError, ValueError):
            invalid.append("AUTHORITY_CHAIN_INVALID")
    for decision in envelope["policy_decisions"]:
        if not isinstance(decision, Mapping):
            invalid.append("POLICY_DECISION_INVALID")
            continue
        try:
            evaluated_at = _timestamp(decision["evaluated_at"])
            if evaluated_at < started - tolerance or evaluated_at > ended + tolerance:
                invalid.append("POLICY_DECISION_OUTSIDE_EXECUTION")
        except (KeyError, TypeError, ValueError):
            invalid.append("POLICY_DECISION_INVALID")
    allowed = envelope["allowed_effects"]
    actual = envelope["actual_effects"]
    if not _string_list(allowed) or not _string_list(actual):
        invalid.append("EFFECT_DECLARATION_INVALID")
    elif not set(actual).issubset(set(allowed)):
        invalid.append("EFFECT_OUTSIDE_AUTHORITY")
    invalid.extend(_budget_reasons(envelope["budget"]))
    revocation = envelope["revocation_check"]
    if not isinstance(revocation, Mapping):
        invalid.append("REVOCATION_CHECK_INVALID")
    elif revocation.get("method") == "none":
        unresolved.append("REVOCATION_NOT_CHECKED")
    if envelope["enforcement_mode"] not in {"fail-closed", "fail-open"}:
        invalid.append("ENFORCEMENT_MODE_INVALID")
    elif envelope["enforcement_mode"] == "fail-open":
        violations = envelope["violations"]
        if not isinstance(violations, list) or not any(
            isinstance(item, Mapping) and item.get("code") == "DEGRADED_ENFORCEMENT"
            for item in violations
        ):
            invalid.append("DEGRADED_ENFORCEMENT_UNDISCLOSED")
    return _unique(invalid), _unique(unresolved)


def _budget_reasons(budget: Any) -> list[str]:
    if not isinstance(budget, Mapping):
        return ["BUDGET_INVALID"]
    granted = budget.get("granted")
    consumed = budget.get("consumed")
    if not isinstance(granted, Mapping) or not isinstance(consumed, Mapping):
        return ["BUDGET_INVALID"]
    reasons: list[str] = []
    for dimension, used in consumed.items():
        if dimension not in granted:
            reasons.append(f"BUDGET_UNGRANTED:{dimension}")
            continue
        try:
            if _quantity(used) > _quantity(granted[dimension]):
                reasons.append(f"BUDGET_EXCEEDED:{dimension}")
        except (TypeError, ValueError, InvalidOperation):
            reasons.append(f"BUDGET_INVALID:{dimension}")
    return reasons


def _quantity(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("EEE quantities cannot be float or bool")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise ValueError("EEE quantity must be an integer or decimal string")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or "." not in value:
        raise ValueError("EEE timestamp must be RFC3339 UTC with milliseconds")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("EEE timestamp must include UTC offset")
    return parsed.astimezone(UTC)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _timestamp_value(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_timestamp(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        return fallback
    return _timestamp_value(parsed)
