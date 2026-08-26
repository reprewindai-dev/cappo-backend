from pycose.messages import Sign1Message
from pycose.keys.okp import OKPKey
import cbor2
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
priv = ed25519.Ed25519PrivateKey.generate()
d = priv.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw, encryption_algorithm=serialization.NoEncryption())
x = priv.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
key = OKPKey(crv='ED25519', d=d, x=x)
msg = Sign1Message(phdr={'ALG': 'EDDSA', 'KID': b'123'}, payload=cbor2.dumps({'test': 'data'}))
msg.key = key
encoded = msg.encode()

# custom decode
cbor_msg = cbor2.loads(encoded)
obj = list(cbor_msg.value)
decoded = Sign1Message.from_cose_obj(obj, True)
decoded.key = OKPKey(crv='ED25519', x=x)
print('Verified:', decoded.verify_signature())
