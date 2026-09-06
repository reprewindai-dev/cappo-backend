
import asyncio
import os

from cappo_backend.security.biscuit import get_root_key_pair

# Delete the existing key
if os.path.exists(os.environ["BISCUIT_ROOT_KEY_PATH"]):
    os.remove(os.environ["BISCUIT_ROOT_KEY_PATH"])

# This will generate and save a brand new key
kp = get_root_key_pair()
