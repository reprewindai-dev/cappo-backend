# Cloud Control Plane + S5 Confidential Computing Forward-Scout Package

**Status:** DESIGN_VALID / implementation handoff only  
**Purpose:** Keep Notebook/architecture work ahead of Gemini/Antigravity while preserving the distinction between candidate design and measured proof.

## Operating model

```text
Notebook / discovery
    -> candidate provider mechanics
    -> mocked control-plane flows
    -> candidate attestation profile
    -> predator batteries
           |
           v
Corrected forward-scout package in GitHub
           |
           v
Gemini / Antigravity
    -> inspect provider-specific APIs/runtime
    -> implement smallest real adapter/profile slice
    -> inject faults
    -> reconcile from authoritative provider/target state
    -> preserve raw evidence
           |
           v
VALID / INVALID / INDETERMINATE
```

Notebook predicts. Implementation attacks. Evidence decides.

## Package contents

- `CLOUD-CONTROL-PLANE-1.md` - corrected cross-provider cloud mutation/finality contract.
- `cloud_control_plane_predator_spec.json` - hostile battery for AWS/GCP/Azure adapter implementations.
- `provider-capability-matrix.md` - provider-specific idempotency, operation-tracking, audit, and finality notes.
- `S5-ATTESTATION-0.md` - corrected hardware-attestation assurance profile.
- `s5_attestation_predator_spec.json` - hostile battery for freshness, measurement, key binding, proxy tampering, and E3 separation.

## Required sequencing

Do not interrupt the current implementation task to start this package. After the Kubernetes/PostgreSQL package is closed against real evidence:

1. Implement one narrow cloud control-plane adapter against one real provider/action.
2. Prove the generic CloudMutationRequest/CloudObservation contract and OUTCOME_UNKNOWN reconciliation semantics.
3. Add a second provider only after adapter semantic isolation is demonstrated.
4. Implement S5 attestation as an assurance profile over execution binding, not as a replacement for E3 consequence finality.
5. Keep provider-specific mechanisms behind stable Veklom contracts.

## Non-negotiable corrections from Notebook

- There is no universal provider idempotency token across AWS, GCP, and Azure or even across all actions within one provider.
- Provider-native idempotency is defense in depth, not a substitute for Veklom ambiguity locking and reconciliation.
- CloudTrail / Cloud Audit Logs / Azure Activity or diagnostic logs are corroborating evidence, not by themselves authoritative target-state finality.
- Finality should prefer provider operation status plus authoritative resource-state re-read, with audit records preserved as supporting evidence.
- `x-ms-client-request-id` is not a universal Azure idempotency guarantee.
- A cloud adapter must bind provider, account/project/subscription, region/scope, API version, resource identity, action, canonical request commitment, and any provider-native operation/idempotency identifiers.
- Nitro Enclaves attestation documents are signed by the Nitro Attestation PKI; AWS KMS is an optional relying service, not the signer of the attestation document.
- S5 attestation proves an attested execution environment and bound key/measurement at a point in time; it does not prove external consequence finality.
- Fresh challenge/nonce binding and attested execution-key binding are mandatory to prevent replay and post-attestation substitution.
- For parent/proxy architectures such as Nitro Enclaves, canonical target requests must remain cryptographically bound across the untrusted proxy boundary.

## Truth precedence

This folder is forward-scout material. Provider documentation and mocked examples are research inputs, not Veklom implementation evidence. Real runtime/provider behavior, exact implementation commits, and independently observed receipts win over every assumption in this package.
