"""Retired VNP data-plane proxy interface.

VNP observes attributable external telemetry; it does not execute public
consequences, synthesize probes, or calculate settlement. The class remains so
existing imports fail closed rather than silently restoring the old proxy.
"""

from __future__ import annotations

from typing import Any


class VNPProxyRetiredError(RuntimeError):
    """Raised whenever obsolete VNP proxy execution is requested."""


class VNPProxyService:
    """Compatibility shell for a retired public execution path."""

    def __init__(self, db: Any, telemetry: Any) -> None:
        self._db = db
        self._telemetry = telemetry

    async def proxy_request(
        self,
        api_did: str,
        payload: dict[str, Any],
        tenant_name: str,
        user_id: Any | None = None,
    ) -> dict[str, Any]:
        """Fail closed; CAPPO owns all consequence-bearing execution."""
        del api_did, payload, tenant_name, user_id
        raise VNPProxyRetiredError(
            "VNP proxy execution is retired; use governed POST /v1/exec"
        )
