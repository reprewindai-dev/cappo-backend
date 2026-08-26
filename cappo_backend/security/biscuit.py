import os
import hashlib
from typing import Optional, Any
from datetime import datetime
import time

import biscuit_auth
from biscuit_auth import KeyPair, Biscuit, AuthorizerBuilder, PrivateKey, PublicKey

from cappo_backend.config import get_settings

_ROOT_KEY_PAIR = None

def get_root_key_pair() -> KeyPair:
    global _ROOT_KEY_PAIR
    if _ROOT_KEY_PAIR is None:
        settings = get_settings()
        biscuit_key = getattr(settings, "BISCUIT_ROOT_PRIVATE_KEY_HEX", None)
        if biscuit_key:
            from biscuit_auth import Algorithm
            _ROOT_KEY_PAIR = KeyPair.from_private_key(PrivateKey.from_bytes(bytes.fromhex(biscuit_key), Algorithm.Ed25519))
        else:
            # Fallback for dev: persist to a file so it survives restart
            key_path = ".biscuit_root_key"
            if os.path.exists(key_path):
                from biscuit_auth import Algorithm
                with open(key_path, "rb") as f:
                    _ROOT_KEY_PAIR = KeyPair.from_private_key(PrivateKey.from_bytes(f.read(), Algorithm.Ed25519))
            else:
                _ROOT_KEY_PAIR = KeyPair()
                with open(key_path, "wb") as f:
                    f.write(_ROOT_KEY_PAIR.private_key.to_bytes())
    return _ROOT_KEY_PAIR

def mint_biscuit_capability(
    caller_spiffe_id: str,
    executor_spiffe_id: str | None,
    capability_id: str,
    reads: list[str],
    writes: list[str],
    execution_id: str,
    ttl_seconds: int,
) -> str:
    kp = get_root_key_pair()
    builder = Biscuit.builder()
    builder.add_code('issuer("veklom");')
    builder.add_code('policy_version(1);')
    builder.add_code('delegation_depth_max(1);')
    builder.add_code(f'subject("{caller_spiffe_id}");')
    builder.add_code('check if current_subject($subj), subject($subj) or current_subject("any");')
    
    builder.add_code(f'execution_id("{execution_id}");')
    builder.add_code(f'capability_id("{capability_id}");')
    if executor_spiffe_id:
        builder.add_code(f'allowed_executor("{executor_spiffe_id}");')
    else:
        builder.add_code('allowed_executor("any");')
    builder.add_code('check if current_executor($exec), allowed_executor($exec) or allowed_executor("any");')
    for r in reads:
        builder.add_code(f'allowed_read("{r}");')
    for w in writes:
        builder.add_code(f'allowed_write("{w}");')
    builder.add_code('check if current_action("read", $res), allowed_read($prefix), $res.starts_with($prefix) or current_action("write", $res), allowed_write($prefix), $res.starts_with($prefix) or current_action("terminate", "");')
    from datetime import datetime, timezone, timedelta
    issued_dt = datetime.now(timezone.utc)
    expires_dt = issued_dt + timedelta(seconds=ttl_seconds)
    
    issued_str = issued_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires_str = expires_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    builder.add_code(f'issued_at("{issued_str}");')
    builder.add_code(f'expires_at("{expires_str}");')
    # Use Biscuit's standard time check
    builder.add_code(f'check if time($time), $time <= {expires_str};')
    token = builder.build(kp.private_key)
    return token.to_base64()

def attenuate_biscuit_capability(
    token_b64: str,
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """Attenuate an existing capability locally without the root key."""
    kp = get_root_key_pair()
    # Notice we only use the public key to parse the token! No private key is used for attenuation.
    token = Biscuit.from_base64(token_b64, kp.public_key)
    builder = biscuit_auth.BlockBuilder()

    if reads is not None or writes is not None:
        if reads:
            for r in reads:
                builder.add_code(f'allowed_read_child("{r}");')
        if writes:
            for w in writes:
                builder.add_code(f'allowed_write_child("{w}");')
        # We append a check that forces actions to ALSO match these tighter constraints
        # It MUST evaluate to true, IN ADDITION to the parent checks.
        builder.add_code('check if current_action("read", $res), allowed_read_child($prefix), $res.starts_with($prefix) or current_action("write", $res), allowed_write_child($prefix), $res.starts_with($prefix) or current_action("terminate", "");')
        
    if ttl_seconds is not None:
        from datetime import datetime, timezone, timedelta
        expires_dt = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        expires_str = expires_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        builder.add_code(f'check if time($time), $time <= {expires_str};')
        
    child_token = token.append(builder)
    return child_token.to_base64()

def verify_biscuit_capability(
    token_b64: str,
    executor_spiffe_id: str,
    action: str,
    resource: str,
    subject_spiffe_id: str | None = None
) -> bool:
    try:
        kp = get_root_key_pair()
        token = Biscuit.from_base64(token_b64, kp.public_key)
        auth_builder = AuthorizerBuilder()
        auth_builder.add_code(f'current_executor("{executor_spiffe_id}");')
        if subject_spiffe_id:
            auth_builder.add_code(f'current_subject("{subject_spiffe_id}");')
        else:
            auth_builder.add_code('current_subject("any");')
        auth_builder.add_code(f'current_action("{action}", "{resource}");')
        auth_builder.set_time()
        auth_builder.add_code('allow if true;')

        # Build the authorizer
        # This will evaluate all checks in all blocks
        # If any check fails, it raises AuthorizationError
        auth = auth_builder.build(token)
        auth.authorize()

        # Enforce delegation_depth_max if present in the token facts
        import biscuit_auth
        depth_facts = auth.query(biscuit_auth.Rule('rule($d) <- delegation_depth_max($d)'))
        if depth_facts:
            # depth_facts[0].terms[0] will contain the integer limit
            max_depth = int(str(depth_facts[0].terms[0]))
            # block_count includes block 0, so depth is block_count - 1
            if token.block_count() - 1 > max_depth:
                print(f"Biscuit verification failed: Delegation depth limit exceeded (max {max_depth}, actual {token.block_count() - 1})")
                return False

        return True
    except Exception as e:
        print(f"Biscuit verification failed: {e}")
        return False
