from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_cert_and_key(filename_prefix, common_name, spiffe_id=None, days_valid=365, issuer_key=None, issuer_name=None, is_ca=False):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    subject_attrs = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    subject = x509.Name(subject_attrs)
    
    if issuer_name is None:
        issuer = subject
    else:
        issuer = issuer_name

    cert_builder = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc) - timedelta(days=1)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=days_valid)
    )

    if is_ca:
        cert_builder = cert_builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )
    
    if spiffe_id:
        cert_builder = cert_builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(spiffe_id)]),
            critical=False
        )

    signing_key = issuer_key if issuer_key else private_key
    cert = cert_builder.sign(signing_key, hashes.SHA256())

    with open(f"{filename_prefix}.key", "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
        
    with open(f"{filename_prefix}.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return private_key, subject, cert

def main():
    print("Generating documented test credentials...")
    
    # 1. Root CA
    ca_key, ca_subject, ca_cert = generate_cert_and_key("test_ca", "Test Root CA", is_ca=True)
    
    # 2. Server
    generate_cert_and_key("server", "server.local", spiffe_id="spiffe://example.org/server", issuer_key=ca_key, issuer_name=ca_subject)
    
    # 3. Valid Client
    generate_cert_and_key("client_valid", "client-valid", spiffe_id="spiffe://example.org/workload/valid", issuer_key=ca_key, issuer_name=ca_subject)
    
    # 4. Wrong SPIFFE
    generate_cert_and_key("client_wrong_spiffe", "client-wrong", spiffe_id="spiffe://example.org/workload/wrong", issuer_key=ca_key, issuer_name=ca_subject)
    
    # 5. Untrusted Client (Self-signed, no CA)
    generate_cert_and_key("client_untrusted", "client-untrusted", spiffe_id="spiffe://example.org/workload/untrusted")
    
    # 6. Expired Client
    # Create manually to set negative validity
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "client-expired")])
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(ca_subject).public_key(private_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(
        datetime.now(timezone.utc) - timedelta(days=10)
    ).not_valid_after(
        datetime.now(timezone.utc) - timedelta(days=5)
    ).add_extension(
        x509.SubjectAlternativeName([x509.UniformResourceIdentifier("spiffe://example.org/workload/expired")]), critical=False
    ).sign(ca_key, hashes.SHA256())

    with open("client_expired.key", "wb") as f:
        f.write(private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.TraditionalOpenSSL, encryption_algorithm=serialization.NoEncryption()))
    with open("client_expired.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        
    # 7. Malformed Client
    with open("client_malformed.key", "w") as f:
        f.write("-----BEGIN PRIVATE KEY-----\nNOT A REAL KEY\n-----END PRIVATE KEY-----\n")
    with open("client_malformed.pem", "w") as f:
        f.write("-----BEGIN CERTIFICATE-----\nNOT A REAL CERT\n-----END CERTIFICATE-----\n")

    print("Test credentials generated.")

if __name__ == "__main__":
    main()
