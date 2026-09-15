# Veklom Active Verification Doctrine

**Status:** Canonical Machine-Enforced Invariant (M1/P2)
**Scope:** Universal Substrate Layer-2 Verifier Module System

## 1. The Invariant
**No `ExecutionIdentityV1` may be minted for physical execution unless a valid `VerifiedRuntimeContext` exists and was created by a registered, trusted `VerifierModule`.**

The orchestrator, the execution payload, the HTTP request, and the workload itself are permanently untrusted regarding physical infrastructure identity. The prey cannot sign its own death certificate.

## 2. The VerifierModule SPI
All substrates (Hyper-V, Linux, KVM, Kubernetes, mTLS) must implement the `VerifierModule` contract:
- `discover()`
- `challenge()`
- `measure()`
- `verify()`
- `attest()`

These modules produce exactly one normalized object: the `VerifiedRuntimeContext`. 

## 3. Evidence Requirements per Substrate
The module system categorizes assurance tiers. Verification modules must extract truthful observations directly from the underlying physical or cryptographic boundary:

*   **Linux Namespaces (Strong Host-Level Assurance):** Must verify `SO_PEERCRED`. Must bind PID start time (via `/proc/<pid>/stat`), boot ID (via `/proc/sys/kernel/random/boot_id`), and cgroup inode to prevent PID recycling and stale execution.
*   **Kubernetes (Strong Orchestrator Assurance):** Must verify the `TokenReview` API. Must extract the private `extra` claims (Pod UID) and independently correlate it with a live fetch of the Pod object and its assigned Node.
*   **Hyper-V / KVM Firecracker (High Assurance):** Must bind the host-side API socket and map the guest's `AF_VSOCK` connection to a host-generated `boot-incarnation nonce`. A static Context ID (`CID`) is insufficient.
*   **Pulsebridge mTLS (Transport Assurance):** Must enforce `CERT_REQUIRED` and extract the short-lived runtime execution lease bound into the certificate SAN/extensions.

## 4. Enforcement
This doctrine is not merely an instruction for developers; it is the machine-enforced architecture of CAPPO. Substrate implementations that construct `runtime_ownership` from unverified parameters or workload-supplied identities will fail independent audit and sink verification rules.
