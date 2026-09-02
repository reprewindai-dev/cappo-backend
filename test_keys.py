from biscuit_auth import Biscuit, KeyPair, PrivateKey, Algorithm

kp1 = KeyPair()
builder = Biscuit.builder()
builder.add_code('issuer("veklom");')
token = builder.build(kp1.private_key)
token_b64 = token.to_base64()

kp2 = KeyPair()
try:
    token2 = Biscuit.from_base64(token_b64, kp2.public_key)
    print("WARNING: Verified with wrong key!")
except Exception as e:
    print(f"SUCCESS: Failed to verify with wrong key: {e}")
