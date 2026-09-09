"""Veklom CAPPO client for Python.

PROVISIONAL CONTRACT-ALIGNED STUB.

This file is intentionally not described as mechanically generated from OpenAPI yet,
because the current Notebook contract draft does not fully define requestBody/security
schemas for all operations. Do not expose or manufacture `Veklom-Authority` headers.
For server/M2M use, pass a scoped bearer token issued through the canonical authority path.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests


class CappoError(Exception):
    def __init__(self, error_class: str, message: str, status_code: int):
        super().__init__(f"[{error_class}] (HTTP {status_code}) {message}")
        self.error_class = error_class
        self.message = message
        self.status_code = status_code


class AuthorityDeniedError(CappoError): pass
class ExecutionIdMismatchError(CappoError): pass
class EnvelopeSubstitutionError(CappoError): pass
class ReplayDeniedError(CappoError): pass
class RetryLockedError(CappoError): pass
class AuthorityLockedError(CappoError): pass
class InfrastructureUnavailableError(CappoError): pass


@dataclass(frozen=True)
class MountResponse:
    mount_id: str
    lease_id: str
    execution_id: str
    envelope_digest: str


@dataclass(frozen=True)
class DispatchResponse:
    dispatch_id: str
    execution_id: str
    status: str
    wal_sequence_id: int


@dataclass(frozen=True)
class ReconciliationResponse:
    execution_id: str
    status: str
    finality_state: str


class CappoClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8002", *, bearer_token: Optional[str] = None, timeout_s: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.timeout_s = timeout_s

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def _handle_error_response(self, response: requests.Response):
        try:
            data = response.json()
            error_class = data.get("error_class", "UnknownError")
            message = data.get("message", response.text)
        except Exception:
            error_class = "RawHttpError"
            message = response.text

        status = response.status_code
        if status in (401, 403):
            raise AuthorityDeniedError(error_class, message, status)
        if status == 422:
            if error_class == "ExecutionIdMismatchError":
                raise ExecutionIdMismatchError(error_class, message, status)
            if error_class == "EnvelopeSubstitutionError":
                raise EnvelopeSubstitutionError(error_class, message, status)
        if status == 423:
            if error_class == "ReplayDeniedError":
                raise ReplayDeniedError(error_class, message, status)
            if error_class == "RetryLockedError":
                raise RetryLockedError(error_class, message, status)
            if error_class == "AuthorityLockedError":
                raise AuthorityLockedError(error_class, message, status)
        if status == 503:
            raise InfrastructureUnavailableError(error_class, message, status)
        raise CappoError(error_class, message, status)

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            f"{self.base_url}{path}",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_s,
        )
        if response.status_code != 200:
            self._handle_error_response(response)
        return response.json()

    def mount_authority(self, capability_id: str, envelope_spec: Dict[str, Any]) -> MountResponse:
        data = self._post("/v1/mounts", {"capability_id": capability_id, "envelope_spec": envelope_spec})
        return MountResponse(**data)

    def dispatch_consequence(self, lease_id: str, execution_id: str, envelope_digest: str, action_payload: Dict[str, Any]) -> DispatchResponse:
        data = self._post("/v1/consequence/dispatch", {
            "lease_id": lease_id,
            "execution_id": execution_id,
            "envelope_digest": envelope_digest,
            "action_payload": action_payload,
        })
        return DispatchResponse(**data)

    def reconcile_consequence(self, execution_id: str) -> ReconciliationResponse:
        data = self._post("/v1/consequence/reconcile", {"execution_id": execution_id})
        return ReconciliationResponse(**data)
