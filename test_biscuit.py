import biscuit_auth
from biscuit_auth import KeyPair, Biscuit, AuthorizerBuilder
from datetime import datetime, timezone, timedelta
kp = KeyPair()
builder = Biscuit.builder()
expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
builder.add_code(f'check if time($time), $time <= {expires};')
token = builder.build(kp.private_key)
auth_builder = AuthorizerBuilder()
auth_builder.set_time()
auth_builder.add_code('allow if true;')
auth = auth_builder.build(token)
print(auth.authorize())
