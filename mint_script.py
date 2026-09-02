
import os
import asyncio
from cappo_backend.security.biscuit import mint_biscuit_capability, get_root_key_pair

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
