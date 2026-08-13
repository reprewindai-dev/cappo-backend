import asyncio
from fastapi.testclient import TestClient
from cappo_backend.main import app

client = TestClient(app)

def test_fpi_router():
    print("Testing Module A: Registration...")
    res = client.post("/api/fpi/providers/register", json={"base_uri": "http://edge1.veklom", "capabilities": ["cap-compute-v1"]})
    if res.status_code != 201:
        print("Error:", res.text)
    assert res.status_code == 201
    prov_id = res.json()["provider_id"]
    print(f"Registered provider: {prov_id}")

    print("\nTesting Module C: Resource Allocation...")
    res = client.post("/api/fpi/resources/allocate", json={"provider_id": prov_id, "compute_units": 10})
    assert res.status_code == 200
    lease_id = res.json()["lease_id"]
    f_max = res.json()["f_max"]
    print(f"Allocated lease: {lease_id} with F_max: {f_max}")

    print("\nTesting Module D: Execution (Missing Auth -> 403)...")
    res = client.post("/api/fpi/execute", json={"lease_id": lease_id})
    assert res.status_code == 403
    print("403 Forbidden properly enforced.")

    print("\nTesting Module D: Execution (Missing Fencing Token -> 428)...")
    res = client.post("/api/fpi/execute", json={"lease_id": lease_id}, headers={"Authorization": "Bearer VALID"})
    assert res.status_code == 428
    print("428 Precondition Required properly enforced.")

    print("\nTesting Module D: Execution (Stale Fencing Token -> 412)...")
    res = client.post("/api/fpi/execute", json={"lease_id": lease_id}, headers={"Authorization": "Bearer VALID", "If-Match": f'"{f_max - 100}"'})
    assert res.status_code == 412
    print("412 Precondition Failed properly enforced.")

    print("\nTesting Module D: Execution (Valid Fencing Token -> 200)...")
    res = client.post("/api/fpi/execute", json={"lease_id": lease_id}, headers={"Authorization": "Bearer VALID", "If-Match": f'"{f_max + 10}"'})
    assert res.status_code == 200
    print(f"200 OK Executed. PGL Receipt: {res.json()['pgl_receipt']}")

    print("\nTesting Module E: Billing...")
    res = client.get("/api/fpi/billing")
    assert res.status_code == 200
    print(f"Billing Ledger: {res.json()}")

if __name__ == "__main__":
    test_fpi_router()
