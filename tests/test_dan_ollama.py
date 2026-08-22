import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

biscuit_auth = pytest.importorskip(
    "biscuit_auth",
    reason="DAN prototype dependency is intentionally not production-installed",
)
dan_router = pytest.importorskip(
    "cappo_backend.api.routers.dan_router",
    reason="DAN prototype is intentionally not production-wired",
)

Biscuit = biscuit_auth.Biscuit
BlockBuilder = biscuit_auth.BlockBuilder
ROOT_KEYPAIR = dan_router.ROOT_KEYPAIR

app = FastAPI()
app.include_router(dan_router.router)

pytestmark = pytest.mark.skip(reason="dan_router is not wired to the production app in P0")


@pytest.mark.anyio
async def test_dan_ollama_missing_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/dan/ollama/api/chat", json={"model": "qwen2.5:3b"})
        assert response.status_code == 401
        assert "Missing Biscuit Token" in response.text


@pytest.mark.anyio
async def test_dan_ollama_invalid_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/dan/ollama/api/chat",
            headers={"Authorization": "Bearer badtoken"},
            json={"model": "qwen2.5:3b"},
        )
        assert response.status_code == 403
        assert "Invalid Cryptographic Chain" in response.text


@pytest.mark.anyio
async def test_dan_ollama_valid_token_rejected_upstream():
    builder = Biscuit.builder()
    builder.add_code('user("test_workspace");')
    builder.add_code('right("ollama", "qwen2.5:3b");')
    base_token = builder.build(ROOT_KEYPAIR.private_key)

    block_builder = BlockBuilder()
    block_builder.add_code('check if operation("generate");')
    block_builder.add_code('check if model("qwen2.5:3b");')
    attenuated = base_token.append(block_builder)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/dan/ollama/api/chat",
            headers={"Authorization": f"Bearer {attenuated.to_base64()}"},
            json={"model": "qwen2.5:3b"},
        )
        assert response.status_code == 503
        assert "PROVIDER_UNAVAILABLE" in response.text


@pytest.mark.anyio
async def test_dan_ollama_invalid_model_attenuation():
    builder = Biscuit.builder()
    builder.add_code('user("test_workspace");')
    builder.add_code('right("ollama", "gpt-4");')
    base_token = builder.build(ROOT_KEYPAIR.private_key)

    block_builder = BlockBuilder()
    block_builder.add_code('check if operation("generate");')
    block_builder.add_code('check if model("qwen2.5:3b");')
    attenuated = base_token.append(block_builder)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/dan/ollama/api/chat",
            headers={"Authorization": f"Bearer {attenuated.to_base64()}"},
            json={"model": "qwen2.5:3b"},
        )
        assert response.status_code == 403
        assert "Datalog Policy Violation" in response.text
