import biscuit_auth
b = biscuit_auth.Biscuit.builder()
b.add_code('delegation_depth_max(1);')
t = b.build(biscuit_auth.KeyPair().private_key)
a = biscuit_auth.AuthorizerBuilder().build(t)
res = a.query(biscuit_auth.Rule('rule($d) <- delegation_depth_max($d)'))
print(res[0].terms[0])
