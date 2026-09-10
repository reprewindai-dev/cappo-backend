# S5-ATTESTATION-0 - Forward-Scout Specification

**Status:** `DESIGN_VALID / NOT YET MEASURED`  
**Scope:** Confidential-computing assurance profile over Veklom execution binding  
**Purpose:** Add hardware-backed execution-environment attestation without confusing attestation with consequence finality.

## 1. Doctrinal Boundary

S5 is an assurance profile, not a new consequence state.

```text
hardware attestation
    proves properties of an attested execution environment

E3 target reconciliation
    proves properties of an external consequence
```

S5 strengthens the execution side of the Consequence Contract. It does not replace E3.

## 2. Canonical Attestation Binding

A valid S5 session should bind a fresh verifier challenge and a Veklom execution key to the attested environment.

Candidate verifier input:

```text
lease_id
authority_digest
execution_id
envelope_digest
canonical_workload_digest
fresh_challenge
attested_execution_public_key_digest
attestation_profile_version
```

The exact carrier is platform-specific. The profile MUST NOT assume all platforms expose the same field names, report-data sizes, PCR model, certificate chain, or verification service.

## 3. Platform Separation

### AWS Nitro Enclaves

Treat the Nitro attestation document as a platform-signed attestation object carrying measurements plus freshness/user binding fields supported by the platform. Verification must validate the Nitro attestation chain/signature and expected measurements. AWS KMS may consume attestation conditions for key release, but KMS is not the signer of the Nitro attestation document.

### AMD SEV-SNP

Treat the SNP attestation report as a hardware-backed report containing guest measurement/state and caller-provided report data. Verification must validate the report signature/certificate chain and expected policy/measurement. `REPORT_DATA` can be used to bind a verifier challenge or execution-key commitment, subject to the platform field-size rules.

### Azure confidential-computing profiles

Azure confidential VM/container offerings can use platform-specific attestation services and evidence formats. The adapter/profile must record the exact TEE type, evidence format, verifier, policy, and key-release semantics used. Do not treat 'Azure confidential' as one universal attestation format.

## 4. Key-Binding Requirement

A nonce alone proves freshness but does not prove that subsequent target requests were emitted by the attested workload.

The preferred S5 flow is:

```text
attested workload creates ephemeral execution keypair
      -> public-key digest included in attestation binding
      -> CAPPO verifies attestation + policy
      -> CAPPO binds lease/execution to that public key
      -> target-bound canonical requests are signed by that execution key
      -> proxy/parent can transport but cannot silently alter canonical request
```

This is especially important for architectures where a parent VM/process proxies network requests for an enclave.

## 5. Freshness and Replay

Every verification session requires a fresh challenge or equivalent freshness mechanism. The verifier must reject:

- stale attestation documents/reports replayed into a new execution;
- a valid attestation bound to another `execution_id` or authority digest;
- an attestation with the expected workload measurement but a different execution public key;
- an attestation generated before a security-relevant authority/policy epoch change when the profile requires freshness against that change.

## 6. Measurement Policy

Measurements are policy inputs, not universally meaningful labels.

The verifier must bind and record:

```text
tee_type
attestation_format
platform_measurements
measurement_policy_id/version
verification_time
freshness/challenge commitment
attested execution public-key digest
lease/authority/envelope commitment
verifier identity/version
verification result/reason
```

An unknown or unsupported measurement/profile is `INDETERMINATE` or `DENY` according to policy; it must not silently fall back to a lower assurance level.

## 7. S5 + E3 Combined Consequence Flow

```text
CAPPO authority
  -> S5 attestation challenge
  -> attestation verified
  -> execution key + workload/envelope bound
  -> canonical consequence signed by attested execution key
  -> provider/target adapter dispatch
  -> executor may disappear
  -> independent target re-observation
  -> E3 finality
  -> evidence preserves both attestation and consequence chain
```

A successful S5 attestation with no target readback does NOT establish consequence finality. A successful E3 readback with no S5 attestation may establish consequence finality at a lower execution-assurance profile.

## 8. Proxy/Parent Threat Boundary

For enclave systems without direct external networking, the parent/proxy is not trusted to redefine the consequence.

The S5 profile must ensure the transport proxy cannot:

- change action or target;
- change canonical payload fields;
- substitute authority/execution identity;
- replay a signed request outside its freshness/lifecycle window;
- substitute a response from a different transition without detection.

Provider transport details may remain outside the enclave, but the policy-relevant canonical request commitment must remain end-to-end bound.

## 9. Seal Gate

`S5-ATTESTATION-0` remains `DESIGN_VALID` until a real supported TEE run proves:

- authentic platform attestation verification;
- fresh challenge binding;
- correct workload/platform measurement policy;
- execution public-key binding;
- lease/authority/envelope binding;
- replay rejection;
- measurement mismatch rejection;
- execution-key substitution rejection;
- proxy/parent request-tamper rejection where a proxy exists;
- explicit separation of S5 attestation success from E3 target consequence success;
- raw attestation object/report, verifier results, policy/version, runtime/platform versions, timestamps, and exact implementation SHA are preserved.

Every hostile test ends `VALID`, `INVALID`, or `INDETERMINATE`.
