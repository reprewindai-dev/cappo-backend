"""The retained VNP proxy service must never execute a public consequence."""

from __future__ import annotations

import asyncio

import pytest

from cappo_backend.services.vnp_proxy_service import VNPProxyRetiredError, VNPProxyService


def test_vnp_proxy_service_is_terminally_retired() -> None:
    service = VNPProxyService(db=None, telemetry=None)  # type: ignore[arg-type]

    with pytest.raises(VNPProxyRetiredError, match="POST /v1/exec"):
        asyncio.run(
            service.proxy_request(
                api_did="did:vnp:api:test",
                payload={"must_not": "execute"},
                tenant_name="test",
            )
        )
