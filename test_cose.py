from cose.messages import Sign1Message
from cose.keys import OKPKey
from cose.headers import Algorithm, KID
from cose.algorithms import EdDSA
from cose.keys.curves import Ed25519
import cbor2

payload = {"test": "data"}
cbor_payload = cbor2.dumps(payload)

key = OKPKey.generate_key(crv=Ed25519, optional_params={'KID': b'123'})
msg = Sign1Message(phdr={Algorithm: EdDSA, KID: b'123'}, payload=cbor_payload)
msg.key = key
encoded = msg.encode()
print('Encoded:', encoded.hex())

decoded = Sign1Message.decode(encoded)
decoded.key = key
print('Verified:', decoded.verify_signature())
