# PROTOTYPE — NOT WIRED TO PRODUCTION APPLICATION
#
# This file is a DAN/Biscuit capability-routing prototype. It is NOT imported
# by main.py and is NOT part of the deployed application.
#
# Biscuit (biscuit_auth) is not in the production dependency manifest.
# This module was removed from main.py as part of P0-0 (restore clean deployable
# baseline). The implementation is preserved in git history and this file for
# future reference when LocalExecutionAuthorizer / LeaseVerifier work begins
# after P0-11.
#
# DO NOT re-import this file in main.py without completing P0-1 through P0-11 first.
import base64
import httpx
from fastapi import APIRouter, Request, Header, HTTPException
from biscuit_auth import KeyPair, Biscuit, AuthorizerBuilder, PublicKey

router = APIRouter()

# For this reference implementation, we generate a fresh Root KeyPair.
# In a real deployed substrate, this would be loaded from a KMS or env var
# and shared with the RICI / Issuer.
# We make it globally accessible here so the mock AAE can build a token with it.
ROOT_KEYPAIR = KeyPair()
ROOT_PUBLIC_KEY = ROOT_KEYPAIR.public_key

@router.post("/dan/ollama/api/chat")
async def dan_ollama_proxy(request: Request, authorization: str = Header(None)):
    """
    Decentralized Authorizer Node (DAN).
    Guards the local Ollama execution boundary utilizing Biscuit capability tokens.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="AUTHORITY_DENIED: Missing Biscuit Token")
    
    token_b64 = authorization[7:]
    
    try:
        token = Biscuit.from_base64(token_b64, ROOT_PUBLIC_KEY)
    except Exception as e:
        raise HTTPException(status_code=403, detail=f"AUTHORITY_DENIED: Invalid Cryptographic Chain: {e}")
    
    body = await request.json()
    model = body.get("model", "qwen2.5:3b")
    
    # Ambient Fact Injection (DAN Context)
    auth_builder = AuthorizerBuilder()
    auth_builder.add_code('operation("generate");')
    auth_builder.add_code(f'model("{model}");')
    auth_builder.add_code('resource("ollama");')
    
    # Verify exact capabilities
    auth_builder.add_code('allow if right("ollama", $m), model($m);')
    
    authorizer = auth_builder.build(token)
    try:
        authorizer.authorize()
    except Exception as e:
        raise HTTPException(status_code=403, detail=f"AUTHORITY_DENIED: Datalog Policy Violation - {e}")
    
    # Proxy to actual local Ollama bypassing external hops (IPC/TCP localhost)
    # This is the actual execution execution layer inside the local node.
    async with httpx.AsyncClient() as client:
        try:
            # Assumes local ollama is on standard port 11434
            response = await client.post("http://localhost:11434/api/chat", json=body, timeout=60.0)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="PROVIDER_UNAVAILABLE")
            return response.json()
        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"PROVIDER_UNAVAILABLE: {e}")

