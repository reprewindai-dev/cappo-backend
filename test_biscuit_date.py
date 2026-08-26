import biscuit_auth
from biscuit_auth import KeyPair, Biscuit, AuthorizerBuilder
kp = KeyPair()
builder = Biscuit.builder()
builder.add_code('check if time($time), $time <= 2027-01-01T00:00:00Z;')
token = builder.build(kp.private_key)
auth_builder = AuthorizerBuilder()
auth_builder.set_time()
auth_builder.add_code('allow if true;')
auth = auth_builder.build(token)
print(auth.authorize())
