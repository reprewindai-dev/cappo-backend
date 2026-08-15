"""VNP must observe governed execution, never become another public executor."""

import asyncio

import pytest
from fastapi import HTTPException

from cappo_backend.api.routers.vnp_router import VNPProxyRequest, vnp_proxy_gateway


def test_vnp_proxy_is_retired_as_an_independent_execution_boundary() -> None:
    async def exercise() -> None:
        with pytest.raises(HTTPException) as raised:
            await vnp_proxy_gateway(
                api_did="did:vnp:api:provider-a",
                request=VNPProxyRequest(payload={"operation": "consequence"}),
                x_vnp_tenant="tenant-a",
                db=object(),
            )

        assert raised.value.status_code == 410
        assert raised.value.detail == "Execution is governed exclusively by POST /v1/exec"

    asyncio.run(exercise())
