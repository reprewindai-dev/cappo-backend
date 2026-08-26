from pycose.messages import Sign1Message
from pycose.keys.ec2 import EC2Key
import cbor2
payload = cbor2.dumps({'test': 'data'})
key = EC2Key.generate_key(crv='P_256')
msg = Sign1Message(phdr={'ALG': 'ES256', 'KID': b'123'}, payload=payload)
msg.key = key
encoded = msg.encode()
print(encoded.hex())
