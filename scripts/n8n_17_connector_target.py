"""N8N-17 governed target worker for the sandbox file connector."""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable

import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from cappo_backend.execution.idempotency_registry import ExecutionState, IdempotencyRegistry
from cappo_backend.execution.kms import GovernedTargetVerifier
from cappo_backend.execution.revocation_registry import LeaseState, RevocationRegistry
from cappo_backend.execution.sandbox_file_connector import (
    ConnectorConflict,
    ConnectorDenied,
    SandboxFileAppendConnector,
)

logger = logging.getLogger(__name__)


class ConnectorRequest(BaseModel):
    action: str
    content: str | None = Field(default=None, max_length=4096)
    compensates_execution_id: str | None = None


class CappoPublicKeyFetcher:
    def __init__(self, base_url: str, internal_token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token
        self._cache = {}

    def __call__(self, kid: str) -> bytes | None:
        if kid in self._cache: return self._cache[kid]
        headers = {"X-API-Key": self.internal_token} if self.internal_token else {}
        try:
            response = httpx.get(f"{self.base_url}/api/v1/execution/keys/{kid}", headers=headers, timeout=3.0)
            if response.status_code == 404: return None
            response.raise_for_status()
            encoded = response.json()["public_key"]
            pub_bytes = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            self._cache[kid] = pub_bytes
            return pub_bytes
        except httpx.ConnectError:
            # If CAPPO is offline, we must fail if not cached
            raise



def create_app(
    *,
    key_fetcher: Callable[[str], bytes | None] | None = None,
    target_path: str | None = None,
    registry: IdempotencyRegistry | None = None,
    revocations: RevocationRegistry | None = None,
) -> FastAPI:
    app = FastAPI(title="Veklom N8N-17 Sandbox Connector")
    fetcher = key_fetcher or CappoPublicKeyFetcher(
        os.environ.get("CAPPO_BASE_URL", "http://127.0.0.1:8002"),
        os.environ.get("CAPPO_INTERNAL_TOKEN", "test-api-key"),
    )
    verifier = GovernedTargetVerifier(fetcher)
    
    default_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scratch", "n8n17", "n8n_governed_append.jsonl"))
    
    connector = SandboxFileAppendConnector(
        target_path or os.environ.get("SANDBOX_APPEND_PATH", default_path)
    )
    executions = registry or IdempotencyRegistry()
    authority_state = revocations or RevocationRegistry(fail_closed=True)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "n8n-17-sandbox-connector"}

    @app.post("/connectors/sandbox-file-append")
    def execute(
        request: ConnectorRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=403, detail="DENY: missing bearer authority")
        token = authorization[7:]
        execution_id: str | None = None
        try:
            claims = verifier.verify(token, audience="sandbox_file_append")
            kid = jwt.get_unverified_header(token).get("kid")
            required = ("jti", "execution_id", "lease_id", "sub")
            if not kid or any(not claims.get(name) for name in required):
                raise ConnectorDenied("DENY: missing required authority claims")
            execution_id = claims["execution_id"]
            state = authority_state.check_authority(
                kid, claims["sub"], claims["lease_id"], execution_id
            )
            if state in (LeaseState.REVOKED, LeaseState.CANCELLING):
                raise ConnectorDenied("DENY: authority revoked or cancelling")

            if request.action == connector.append_action:
                if request.content is None or request.compensates_execution_id is not None:
                    raise ConnectorDenied("DENY: invalid append request shape")
            elif request.action == connector.compensate_action:
                if request.compensates_execution_id is None or request.content is not None:
                    raise ConnectorDenied("DENY: invalid compensation request shape")
            else:
                raise ConnectorDenied("DENY: unsupported connector action")

            action_data = request.model_dump(exclude_none=True)
            is_new, error, previous = executions.reserve(claims["jti"], execution_id, action_data)
            if not is_new:
                if previous:
                    response.headers["X-Veklom-Receipt-ID"] = previous["receipt_id"]
                    return previous
                raise ConnectorConflict(error or "execution already in progress")
            executions.update_state(execution_id, ExecutionState.RUNNING)

            state = authority_state.check_authority(
                kid, claims["sub"], claims["lease_id"], execution_id
            )
            if state in (LeaseState.REVOKED, LeaseState.CANCELLING):
                executions.update_state(execution_id, ExecutionState.CANCELLED)
                raise ConnectorDenied("DENY: cancelled before consequence")

            if request.action == connector.append_action:
                receipt = connector.append(claims, request.content or "")
            else:
                receipt = connector.compensate(claims, request.compensates_execution_id or "")

            result = {
                "status": "SUCCESS",
                "execution_id": execution_id,
                "receipt_id": receipt.operation_id,
                "evidence": receipt.as_dict(),
            }
            executions.update_state(execution_id, ExecutionState.SUCCEEDED, result)
            response.headers["X-Veklom-Receipt-ID"] = receipt.operation_id
            return result
        except ConnectorConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConnectorDenied as exc:
            logger.warning("Connector request denied: %s", type(exc).__name__)
            if execution_id:
                executions.update_state(execution_id, ExecutionState.FAILED_TERMINAL)
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            # Never decode or log an untrusted bearer token. Even an unsigned
            # payload can carry workspace, lease, resource, and budget data.
            logger.exception("Authority verification failed")
            raise HTTPException(status_code=403, detail="DENY: authority verification failed") from exc

    @app.get("/connectors/sandbox-file-append/status/{execution_id}")
    def status(execution_id: str) -> dict:
        record = connector.reconcile(execution_id)
        if record is None:
            raise HTTPException(status_code=404, detail="No positive connector evidence")
        return record

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8099)




