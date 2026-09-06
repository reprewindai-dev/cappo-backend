
import asyncio
import os

from cappo_backend.security.biscuit import get_root_key_pair, mint_biscuit_capability


async def run():
    kp = get_root_key_pair()
    token = mint_biscuit_capability(
        "spiffe://test/caller", 
        "spiffe://test/executor", 
        "cap_123", 
        ["record.read"], 
        ["record.write"], 
        "test_exec_123", 
        3600
    )
    print(token)

asyncio.run(run())
