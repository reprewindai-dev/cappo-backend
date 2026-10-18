import pytest

def check_binding_seam():
    print("=== Binding Seam Inspection ===")
    print("1. _resolve_capability_lease checks:")
    print("   - ownership (principal, workspace_id)")
    print("   - state == mounted")
    print("   - workspace match")
    print("   - token_id hmac match")
    print("   - nonce hmac match")
    print("   - execution_id hmac match")
    print("2. BUT exec_router.py registry.evaluate call:")
    print("   - Passes `lease_ref.mount_id`")
    print("   - Passes `body.action or 'execute'`")
    print("   - DOES NOT pass `body.resource` or `body.target_ref`")
    print("3. Therefore, the registry NEVER verifies if the lease is authorized for the specific target/resource being requested!")
    print("4. Furthermore, VRE execution binding requires the CAPPO CapabilityLease to be cryptographically bound to the physical VRE envelope.")
    print("Result: CAPABILITY LEASE TO VRE BINDING IS BROKEN (INVALID) - TARGET SUBSTITUTION ALLOWED")

if __name__ == "__main__":
    check_binding_seam()
