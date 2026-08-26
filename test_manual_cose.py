import cbor2
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

# Generate key
priv = ed25519.Ed25519PrivateKey.generate()
pub = priv.public_key()

payload = b'test_payload'
protected_header = cbor2.dumps({1: -8}) # ALG: EdDSA
unprotected_header = {4: b'my-key'} # KID

# Sig_structure
sig_structure = ['Signature1', protected_header, b'', payload]
sig_data = cbor2.dumps(sig_structure)

# Sign
signature = priv.sign(sig_data)

# COSE_Sign1 object
cose_sign1 = [protected_header, unprotected_header, payload, signature]
cose_bytes = cbor2.dumps(cbor2.CBORTag(18, cose_sign1))

print('COSE Bytes:', cose_bytes.hex())

# Verify
decoded_tag = cbor2.loads(cose_bytes)
assert decoded_tag.tag == 18
ph, uh, p, sig = decoded_tag.value
sig_struct = ['Signature1', ph, b'', p]
pub.verify(sig, cbor2.dumps(sig_struct))
print('Verified manually!')
