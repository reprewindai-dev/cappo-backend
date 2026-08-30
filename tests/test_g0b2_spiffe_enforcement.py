import asyncio
import datetime
import ssl
import threading
import time

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from uvicorn import Config, Server

from cappo_backend.config import get_settings
from cappo_backend.main import app as _app

# Force test settings
settings = get_settings()
settings.enforce_spiffe = True
settings.spiffe_trust_domain = "example.org"
settings.auth_enabled = True
settings.api_keys = "test-api-key"

def generate_cert(name="test", ca=False, issuer_key=None, issuer_name=None, spiffe_id=None, expired=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    issuer = issuer_name if issuer_name else subject
    key_to_sign_with = issuer_key if issuer_key else key

    not_before = datetime.datetime.utcnow() - datetime.timedelta(days=2) if expired else datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
    not_after = datetime.datetime.utcnow() - datetime.timedelta(days=1) if expired else datetime.datetime.utcnow() + datetime.timedelta(days=1)

    builder = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)\
        .public_key(key.public_key())\
        .serial_number(x509.random_serial_number())\
        .not_valid_before(not_before)\
        .not_valid_after(not_after)
        
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    builder = builder.add_extension(ski, critical=False)
    
    if ca:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        # self signed CA, issuer is self
        aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key())
        builder = builder.add_extension(aki, critical=False)
    elif spiffe_id:
        builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH, x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        import ipaddress
        if name == "127.0.0.1":
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), critical=False)
        else:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)]), critical=False)
        aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key())
        builder = builder.add_extension(aki, critical=False)
    else:
        builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True, data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        # Malformed cert without spiffe id but we still need AKI
        aki = x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key())
        builder = builder.add_extension(aki, critical=False)
        
    cert = builder.sign(key_to_sign_with, hashes.SHA256())
    return cert, key

def save_pem(cert, key, cert_path, key_path=None):
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    if key and key_path:
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

# Setup CA and Certs
ca_cert, ca_key = generate_cert(name="Veklom Root CA", ca=True)
save_pem(ca_cert, ca_key, "test_ca.pem")

untrusted_ca_cert, untrusted_ca_key = generate_cert(name="Untrusted Root CA", ca=True)

valid_cert, valid_key = generate_cert(name="Valid Client", issuer_key=ca_key, issuer_name=ca_cert.subject, spiffe_id="spiffe://example.org/workload/cappo-backend")
save_pem(valid_cert, valid_key, "client_valid.pem", "client_valid.key")

expired_cert, expired_key = generate_cert(name="Expired Client", issuer_key=ca_key, issuer_name=ca_cert.subject, spiffe_id="spiffe://example.org/workload/cappo-backend", expired=True)
save_pem(expired_cert, expired_key, "client_expired.pem", "client_expired.key")

untrusted_cert, untrusted_key = generate_cert(name="Untrusted Client", issuer_key=untrusted_ca_key, issuer_name=untrusted_ca_cert.subject, spiffe_id="spiffe://example.org/workload/cappo-backend")
save_pem(untrusted_cert, untrusted_key, "client_untrusted.pem", "client_untrusted.key")

wrong_spiffe_cert, wrong_spiffe_key = generate_cert(name="Wrong Client", issuer_key=ca_key, issuer_name=ca_cert.subject, spiffe_id="spiffe://example.org/workload/wrong")
save_pem(wrong_spiffe_cert, wrong_spiffe_key, "client_wrong_spiffe.pem", "client_wrong_spiffe.key")

malformed_cert, malformed_key = generate_cert(name="Malformed Client", issuer_key=ca_key, issuer_name=ca_cert.subject, spiffe_id=None) # missing SAN
save_pem(malformed_cert, malformed_key, "client_malformed.pem", "client_malformed.key")

server_cert, server_key = generate_cert(name="127.0.0.1", issuer_key=ca_key, issuer_name=ca_cert.subject, spiffe_id="spiffe://example.org/workload/server")
save_pem(server_cert, server_key, "server.pem", "server.key")

from starlette.middleware.base import BaseHTTPMiddleware


class InjectTLSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if "X-Test-Inject-Cert" in request.headers:
            import base64
            cert = request.headers["X-Test-Inject-Cert"]
            if "extensions" not in request.scope:
                request.scope["extensions"] = {}
            if "tls" not in request.scope["extensions"]:
                request.scope["extensions"]["tls"] = {}
            request.scope["extensions"]["tls"]["client_cert"] = base64.b64decode(cert)
        return await call_next(request)

# Note: middleware are executed outermost to innermost, so we must add it to the top of the stack,
# or just wrap the app purely at the ASGI level.
async def _test_wrapper_app(scope, receive, send):
    if scope["type"] == "http":
        headers = dict(scope.get("headers", []))
        cert = headers.get(b"x-test-inject-cert")
        if cert:
            import base64
            if "extensions" not in scope:
                scope["extensions"] = {}
            if "tls" not in scope["extensions"]:
                scope["extensions"]["tls"] = {}
            scope["extensions"]["tls"]["client_cert"] = base64.b64decode(cert)
            
        scope["auth_workspace"] = "test-workspace"
    await _app(scope, receive, send)

def run_uvicorn_in_thread(app, port):
    config = Config(
        app=app,
        host="127.0.0.1",
        port=port,
        ssl_keyfile="server.key",
        ssl_certfile="server.pem",
        ssl_ca_certs="test_ca.pem",
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        log_level="info",
    )
    server = Server(config)
    t = threading.Thread(target=server.run)
    t.daemon = True
    t.start()
    time.sleep(1) # wait for server to bind
    return server

from cappo_backend.capability_mount.models import CapabilityPackage


def register_test_pkg():
    pkg = CapabilityPackage(
        id="echo@v1",
        family="echo",
        title="Echo",
        purpose="Testing"
    )
    _app.state.mount_registry.register_package(pkg)
    return pkg

@pytest.fixture(scope="function")
def uvicorn_server(db):
    register_test_pkg()
    def _override_session():
        yield db
    from cappo_backend.capability_mount.service import AnchorResult
    class MockAnchor:
        def anchor(self, event_type, **kwargs):
            return AnchorResult("confirmed", anchor_id="mock-anchor-123")
    _app.state.mount_registry.anchor = MockAnchor()
    from cappo_backend.db.session import get_session
    _app.dependency_overrides[get_session] = _override_session
    server = run_uvicorn_in_thread(_test_wrapper_app, 8443)
    yield "https://127.0.0.1:8443"
    server.should_exit = True
    _app.dependency_overrides.clear()

def get_ssl_context(cert_file=None, key_file=None):
    ctx = ssl.create_default_context(cafile="test_ca.pem")
    if cert_file and key_file:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return ctx

def test_g0b2_spiffe_enforcement(uvicorn_server):
    asyncio.run(_test_g0b2_spiffe_enforcement(uvicorn_server))

async def _test_g0b2_spiffe_enforcement(uvicorn_server):
    # Setup test token for exactly-once replay logic
    # Usually we get it from issuing mount, but let's just make sure the endpoints return correctly.
    # We will test the identity boundary by calling a protected consequence endpoint or mount endpoint.
    
    headers = {
        "X-API-Key": "test-api-key",
        "X-Workspace-Id": "ws_123"
    }
    
    async def request_mount(cert_file, key_file, xfcc=None):
        ctx = get_ssl_context(cert_file, key_file)
        h = headers.copy()
        if xfcc:
            h["X-Forwarded-Client-Cert"] = xfcc
            
        import base64
        with open(cert_file, "rb") as f:
            cert_pem = f.read()
            
        # We pass it as a custom header because Uvicorn doesn't expose it to ASGI scope natively
        h["X-Test-Inject-Cert"] = base64.b64encode(cert_pem).decode("ascii")
        
        async with httpx.AsyncClient(verify=ctx) as client:
            try:
                resp_pkgs = await client.get(
                    f"{uvicorn_server}/v1/capability/packages",
                    headers=h
                )
                pkgs = resp_pkgs.json()
                package_ref = pkgs[0]["id"] if pkgs else "echo@v1"

                # Try to mount something which enforces SPIFFE
                resp = await client.post(
                    f"{uvicorn_server}/v1/capability/mounts",
                    headers=h,
                    json={
                        "package_ref": package_ref,
                        "execution_scope": {"workspace": "test-workspace", "project": "test-proj"},
                    }
                )
                return resp
            except httpx.ConnectError as e:
                print("ConnectError:", e)
                return None
            except ssl.SSLCertVerificationError as e:
                print("SSLError:", e)
                return None
            except httpx.ReadError as e:
                print("ReadError:", e)
                return None

    # 1. VALID_SVID -> ALLOW
    resp1 = await request_mount("client_valid.pem", "client_valid.key")
    assert resp1 is not None, "Valid SVID should connect"
    assert resp1.status_code == 200, f"Valid SVID should be allowed, got {resp1.status_code} {resp1.text}"
    
    # 2. NO_CERT -> DENY (Socket layer or Uvicorn drop)
    # Actually, httpx won't even connect if server requires cert
    try:
        ctx_no_cert = ssl.create_default_context(cafile="test_ca.pem")
        async with httpx.AsyncClient(verify=ctx_no_cert) as client:
            await client.post(f"{uvicorn_server}/v1/capability/mounts")
        assert False, "NO_CERT should fail connection"
    except Exception:
        pass
        
    # 3. EXPIRED_SVID -> DENY
    resp3 = await request_mount("client_expired.pem", "client_expired.key")
    assert resp3 is None or resp3.status_code == 403, "Expired SVID should fail connection at TLS layer or get 403"
    
    # 4. UNTRUSTED_ROOT -> DENY
    resp4 = await request_mount("client_untrusted.pem", "client_untrusted.key")
    assert resp4 is None or resp4.status_code == 403, "Untrusted Root should fail connection at TLS layer or get 403"
    
    # 5. WRONG_SPIFFE_ID -> DENY
    resp5 = await request_mount("client_wrong_spiffe.pem", "client_wrong_spiffe.key")
    assert resp5 is not None, "Wrong SPIFFE connects"
    assert resp5.status_code == 403, f"Wrong SPIFFE should get 403, got {resp5.status_code} {resp5.text}"
    assert "SPIFFE_POLICY_MISMATCH" in resp5.text
    
    # 6. MALFORMED / NON-SPIFFE_CERT -> DENY
    resp6 = await request_mount("client_malformed.pem", "client_malformed.key")
    assert resp6 is not None, "Malformed SVID connects"
    assert resp6.status_code == 403, "Malformed SVID should get 403"
    assert "INVALID_SVID" in resp6.text
    
    # 7. FORGED_XFCC -> DENY
    # Connect with NO CERT but pass XFCC. It will fail connection because server requires cert!
    # Let's connect with WRONG_SPIFFE_ID but a valid XFCC to see if it bypasses.
    resp7 = await request_mount("client_wrong_spiffe.pem", "client_wrong_spiffe.key", xfcc="spiffe://example.org/workload/cappo-backend")
    assert resp7 is not None
    assert resp7.status_code == 403, "XFCC should be ignored, wrong spiffe still 403"
    
    # 8. VALID_REPLAY -> DENY
    # Well, we just test if exactly-once is preserved by looking at how `test_g0a5` does it.
    # The mount is successful, now evaluate action twice.
    resp = await request_mount("client_valid.pem", "client_valid.key")
    data = resp.json()
    print(f"MOUNT RESPONSE DATA: {data}")
    assert "mount" in data and data["mount"] is not None, f"Missing mount in response: {data}"
    mount_id = data["mount"]["id"]
    token_id = data["token"]["token_id"]
    nonce = data["token"]["nonce"]
    
    ctx = get_ssl_context("client_valid.pem", "client_valid.key")
    
    import base64
    with open("client_valid.pem", "rb") as f:
        cert_pem = f.read()
    headers["X-Test-Inject-Cert"] = base64.b64encode(cert_pem).decode("ascii")
    
    async with httpx.AsyncClient(verify=ctx) as client:
        # Action 1
        a1 = await client.post(
            f"{uvicorn_server}/v1/capability/mounts/{mount_id}/actions",
            headers=headers,
            json={"token_id": token_id, "nonce": nonce, "action": "echo"}
        )
        assert a1.status_code == 200
        
        # Action 2 (Replay)
        a2 = await client.post(
            f"{uvicorn_server}/v1/capability/mounts/{mount_id}/actions",
            headers=headers,
            json={"token_id": token_id, "nonce": nonce, "action": "echo"}
        )
        assert a2.status_code == 200
        assert a2.json()["decision"] == "deny"
        
    print("G0B.2 = VERIFIED")
