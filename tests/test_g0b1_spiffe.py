import os

import pytest
from spiffe import WorkloadApiClient


def test_spiffe_workload_api():
    socket_path = os.environ.get("SPIFFE_ENDPOINT_SOCKET")
    if not socket_path:
        pytest.skip("SPIFFE_ENDPOINT_SOCKET not set, skipping live SPIRE test")

    # The client automatically discovers the socket path from SPIFFE_ENDPOINT_SOCKET if not provided
    with WorkloadApiClient() as client:
        x509_svid = client.fetch_x509_svid()
        
        # Verify it has a valid spiffe_id
        spiffe_id = str(x509_svid.spiffe_id)
        assert spiffe_id.startswith("spiffe://"), f"Invalid SPIFFE ID: {spiffe_id}"
        
        print("\n=== SVID OBTAINED ===")
        print(f"SPIFFE ID: {spiffe_id}")
        
        # SVIDs also have the actual certificate and private key
        cert_pem = x509_svid.cert_chain[0]
        print(f"Certificate Subject: {cert_pem.subject}")
        print(f"Certificate Issuer: {cert_pem.issuer}")
        print("=== TEST PASSED ===")

if __name__ == "__main__":
    test_spiffe_workload_api()
