"""CAPPO executor adapter for Lockerphycer governed execution cells.

The ordinary provider executor remains available for non-cell capabilities. A
``github.file.update`` consequence never falls back to a provider executor: it
must pass through the signed Lockerphycer cell path or fail closed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from cappo_backend.services.cell_authority import CellAuthorityBuilder, CellAuthorityError
from cappo_backend.services.executor import Executor, TerminalExecutionError


class GovernedCellExecutionError(TerminalExecutionError):
    """A governed-cell consequence could not satisfy its execution boundary."""

    error_code = "GOVERNED_CELL_EXECUTION_DENIED"


class LockerphycerCellExecutor:
    """Call the host-only Lockerphycer cell service over a Unix-domain socket."""

    provider = "lockerphycer-governed-cell"

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.socket_path = os.environ.get(
            "CAPPO_LOCKERPHYCER_CELL_SOCKET",
            "/run/lockerphycer/cell-host.sock",
        ).strip()
        self.host_api_key = os.environ.get("CAPPO_LOCKERPHYCER_CELL_HOST_API_KEY", "").strip()
        self.image = os.environ.get("CAPPO_GOVERNED_CELL_IMAGE", "").strip()
        raw_command = os.environ.get("CAPPO_GOVERNED_CELL_COMMAND_JSON", "").strip()

        if not self.socket_path:
            raise GovernedCellExecutionError("Lockerphycer cell socket is not configured")
        if len(self.host_api_key) < 32:
            raise GovernedCellExecutionError("Lockerphycer cell-host credential is not configured")
        if "@sha256:" not in self.image:
            raise GovernedCellExecutionError("governed-cell executor image must be pinned by digest")
        try:
            command = json.loads(raw_command)
        except json.JSONDecodeError as exc:
            raise GovernedCellExecutionError("CAPPO_GOVERNED_CELL_COMMAND_JSON is invalid") from exc
        if not isinstance(command, list) or not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise GovernedCellExecutionError("governed-cell command must be a non-empty JSON string array")
        self.command = command

        if client is None:
            transport = httpx.HTTPTransport(uds=self.socket_path, retries=0)
            client = httpx.Client(
                transport=transport,
                base_url="http://lockerphycer-cell-host",
                timeout=45.0,
                follow_redirects=False,
            )
        self.client = client

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.post(
                path,
                json=payload,
                headers={"X-Cell-Host-Key": self.host_api_key},
            )
        except httpx.HTTPError as exc:
            raise GovernedCellExecutionError("Lockerphycer cell host is unavailable") from exc
        if response.status_code != 200:
            raise GovernedCellExecutionError(
                f"Lockerphycer cell host rejected governed execution (HTTP {response.status_code})"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise GovernedCellExecutionError("Lockerphycer cell host returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise GovernedCellExecutionError("Lockerphycer cell host returned an invalid response shape")
        return body

    def execute(self, request: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
        effect = request.get("effect")
        if not isinstance(effect, dict):
            raise GovernedCellExecutionError("governed GitHub execution requires an exact effect object")
        envelope = authority.get("envelope") if isinstance(authority, dict) else None
        if not isinstance(envelope, dict):
            raise GovernedCellExecutionError("CAPPO cell authority envelope is missing")

        required_isolation = envelope.get("required_isolation")
        if required_isolation not in {"os-enforced", "microvm"}:
            raise GovernedCellExecutionError("CAPPO cell authority has invalid required isolation")
        configured_digest = self.image.rsplit("@", 1)[-1]
        if envelope.get("runtime_image_digest") != configured_digest:
            raise GovernedCellExecutionError("configured executor image does not match signed CAPPO authority")
        if required_isolation == "microvm" and not envelope.get("runtime_kernel_digest"):
            raise GovernedCellExecutionError("hard-isolated authority is missing its signed kernel measurement")

        # The untrusted workload receives only the proposed effect. It receives no
        # provider credential and no routable network. The host broker is a later,
        # separate stage after successful teardown of the hostile workload.
        cell_result = self._post(
            "/v1/cells/run",
            {
                "authority": authority,
                "image": self.image,
                "command": self.command,
                "input_payload": effect,
                "safe_environment": {},
            },
        )
        if cell_result.get("timed_out") is not False:
            raise GovernedCellExecutionError("governed cell timed out")
        if cell_result.get("exit_code") != 0:
            raise GovernedCellExecutionError("governed cell did not exit successfully")
        if cell_result.get("teardown_confirmed") is not True:
            raise GovernedCellExecutionError("governed cell teardown was not confirmed")
        if cell_result.get("network_mode") != "none":
            raise GovernedCellExecutionError("governed cell did not prove network-none mode")
        if cell_result.get("credential_mode") != "brokered_only":
            raise GovernedCellExecutionError("governed cell credential mode is not broker-only")
        if cell_result.get("isolation_class") != required_isolation:
            raise GovernedCellExecutionError("governed cell isolation class does not match signed authority")
        if required_isolation == "microvm" and not cell_result.get("runtime_measurement"):
            raise GovernedCellExecutionError("microVM execution did not provide a runtime measurement")

        try:
            proposed_effect = json.loads(cell_result.get("stdout") or "")
        except json.JSONDecodeError as exc:
            raise GovernedCellExecutionError("governed cell did not emit a structured effect") from exc
        if proposed_effect != effect:
            raise GovernedCellExecutionError("governed cell attempted to alter the authorized effect")

        effect_result = self._post(
            "/v1/effects/github/file-update",
            {**effect, "authority": authority},
        )
        if effect_result.get("mutation_succeeded") is not True:
            raise GovernedCellExecutionError("GitHub effect was not confirmed")
        if effect_result.get("originating_cell_id") != cell_result.get("cell_id"):
            raise GovernedCellExecutionError("brokered effect is not bound to the completed cell")
        if effect_result.get("required_isolation") != required_isolation:
            raise GovernedCellExecutionError("brokered effect isolation evidence does not match authority")

        # A target can accept a real consequence even when later evidence or
        # credential-revocation confirmation is incomplete. Never erase that fact;
        # propagate the incident into PGL instead of turning it into a retryable
        # execution failure.
        revocation_confirmed = effect_result.get("credential_revoked") is True
        target_result_confirmed = effect_result.get("target_result_confirmed") is True
        security_status = effect_result.get("security_status") or "UNVERIFIED"

        return {
            "response": json.dumps(effect_result, sort_keys=True, separators=(",", ":")),
            "model": None,
            "provider": self.provider,
            "tokens": 0,
            "governed_cell": {
                "cell_id": cell_result.get("cell_id"),
                "runtime": cell_result.get("runtime"),
                "isolation_class": cell_result.get("isolation_class"),
                "runtime_measurement": cell_result.get("runtime_measurement"),
                "network_policy_digest": cell_result.get("network_policy_digest"),
                "authority_digest": cell_result.get("authority_digest"),
                "started_at": cell_result.get("started_at"),
                "completed_at": cell_result.get("completed_at"),
                "network_mode": "none",
                "credential_mode": "brokered_only",
                "teardown_confirmed": True,
            },
            "effect": effect_result,
            "security_status": security_status,
            "credential_revocation_confirmed": revocation_confirmed,
            "target_result_confirmed": target_result_confirmed,
        }


class GovernedCellDispatchExecutor:
    """Route only cell-qualified consequences to Lockerphycer; never fallback."""

    def __init__(
        self,
        *,
        inner: Executor,
        settings: Any,
        db: Any,
        cell: LockerphycerCellExecutor | None = None,
    ) -> None:
        self.inner = inner
        self.db = db
        self.authority = CellAuthorityBuilder(settings)
        self.cell = cell or LockerphycerCellExecutor()

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("action") != "github.file.update":
            return self.inner.execute(request)

        try:
            authority = self.authority.build_from_execution_request(request, self.db)
        except CellAuthorityError as exc:
            raise GovernedCellExecutionError(str(exc)) from exc

        # No exception path here falls back to ``inner``. A governed GitHub
        # consequence either satisfies the cell boundary or terminates.
        return self.cell.execute(request, authority)
