import asyncio
import ssl
import httpx
from fastapi import FastAPI, Request
from uvicorn import Config, Server
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtensionOID
import datetime
import threading
import traceback

def generate_cert(name="test", ca=False, issuer_key=None, issuer_name=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer = issuer_name if issuer_name else subject
    key_to_sign_with = issuer_key if issuer_key else key
    
    builder = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)\
        .public_key(key.public_key())\
        .serial_number(x509.random_serial_number())\
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))\
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
        
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    else:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier("spiffe://example.org/workload/test")]), critical=False)
        
    cert = builder.sign(key_to_sign_with, hashes.SHA256())
    return cert, key

ca_cert, ca_key = generate_cert(name="CA", ca=True)
client_cert, client_key = generate_cert(name="Client", ca=False, issuer_key=ca_key, issuer_name=ca_cert.subject)
server_cert, server_key = generate_cert(name="localhost", ca=False, issuer_key=ca_key, issuer_name=ca_cert.subject)

with open("ca.pem", "wb") as f:
    f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
with open("client.pem", "wb") as f:
    f.write(client_cert.public_bytes(serialization.Encoding.PEM))
    f.write(client_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
with open("server.pem", "wb") as f:
    f.write(server_cert.public_bytes(serialization.Encoding.PEM))
    f.write(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

app = FastAPI()

@app.get("/")
async def index(request: Request):
    tls = request.scope.get("extensions", {}).get("tls", {})
    # It might be in bytes or string
    cc = tls.get("client_cert")
    if cc: cc = cc.decode("utf-8") if isinstance(cc, bytes) else str(cc)
    return {"tls_keys": list(tls.keys()), "client_cert_type": str(type(tls.get("client_cert"))), "cert": cc[:50] if cc else None}

async def run_server():
    config = Config(app=app, host="127.0.0.1", port=8443, ssl_keyfile="server.pem", ssl_certfile="server.pem", ssl_ca_certs="ca.pem", ssl_cert_reqs=ssl.CERT_REQUIRED)
    server = Server(config)
    await server.serve()

def start_server():
    asyncio.run(run_server())

t = threading.Thread(target=start_server, daemon=True)
t.start()
import time
time.sleep(2)

ssl_context = ssl.create_default_context()
ssl_context.load_verify_locations("ca.pem")
ssl_context.load_cert_chain("client.pem")
try:
    with httpx.Client(verify=ssl_context) as client:
        r = client.get("https://127.0.0.1:8443/")
        print("Success:", r.json())
except Exception as e:
    print("Error:", e)
    traceback.print_exc()
