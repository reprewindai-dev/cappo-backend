import pytest
pytestmark = pytest.mark.skip(reason="dan_router is not wired to the production app in P0")


from cappo_backend.api.routers.dan_router import ROOT_KEYPAIR

@pytest.mark.anyio
async def test_dan_ollama_missing_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/dan/ollama/api/chat", json={"model": "qwen2.5:3b"})
        assert response.status_code == 401
        assert "Missing Biscuit Token" in response.text

@pytest.mark.anyio
async def test_dan_ollama_invalid_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/dan/ollama/api/chat", headers={"Authorization": "Bearer badtoken"}, json={"model": "qwen2.5:3b"})
        assert response.status_code == 403
        assert "Invalid Cryptographic Chain" in response.text

@pytest.mark.anyio
async def test_dan_ollama_valid_token_rejected_upstream(monkeypatch):
    builder = Biscuit.builder()
    builder.add_code('user("test_workspace");')
    builder.add_code('right("ollama", "qwen2.5:3b");')
    base_token = builder.build(ROOT_KEYPAIR.private_key)
    
    # Attenuate for specific model and operation
    block_builder = BlockBuilder()
    block_builder.add_code('check if operation("generate");')
    block_builder.add_code('check if model("qwen2.5:3b");')
    attenuated = base_token.append(block_builder)
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Since local ollama won't be running, it should return 503 PROVIDER_UNAVAILABLE
        response = await ac.post("/dan/ollama/api/chat", headers={"Authorization": f"Bearer {attenuated.to_base64()}"}, json={"model": "qwen2.5:3b"})
        assert response.status_code == 503
        assert "PROVIDER_UNAVAILABLE" in response.text

@pytest.mark.anyio
async def test_dan_ollama_invalid_model_attenuation(monkeypatch):
    builder = Biscuit.builder()
    builder.add_code('user("test_workspace");')
    builder.add_code('right("ollama", "gpt-4");') # Has rights to gpt-4, not qwen
    base_token = builder.build(ROOT_KEYPAIR.private_key)
    
    # Attenuate for specific model and operation
    block_builder = BlockBuilder()
    block_builder.add_code('check if operation("generate");')
    block_builder.add_code('check if model("qwen2.5:3b");')
    attenuated = base_token.append(block_builder)
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/dan/ollama/api/chat", headers={"Authorization": f"Bearer {attenuated.to_base64()}"}, json={"model": "qwen2.5:3b"})
        # Should fail DAN auth!
        assert response.status_code == 403
        assert "Datalog Policy Violation" in response.text

