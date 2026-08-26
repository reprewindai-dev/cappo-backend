import biscuit_auth
from biscuit_auth import KeyPair, Biscuit, AuthorizerBuilder
kp = KeyPair()
builder = Biscuit.builder()
builder.add_code('allowed_read("/records/");')
builder.add_code('check if current_action("read", $res), allowed_read($prefix), $res.starts_with($prefix);')
token = builder.build(kp.private_key)
auth_builder = AuthorizerBuilder()
auth_builder.add_code('current_action("read", "/records/customer-42");')
auth_builder.add_code('allow if true;')
auth = auth_builder.build(token)
print(auth.authorize())
