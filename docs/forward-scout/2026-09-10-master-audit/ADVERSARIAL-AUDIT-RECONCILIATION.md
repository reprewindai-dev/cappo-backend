# Adversarial Audit Reconciliation — 2026-09-10

**Status:** `CORRECTION ACCEPTED / PACKET CERTIFICATION REJECTED / REPO TRUTH PRESERVED`

## Scope

This document reconciles the external adversarial review of the Notebook-generated Cloud/S5/Kubernetes/PostgreSQL evidence packet against current Veklom repository truth.

The audit correctly demonstrates that the supplied packet is **not certifiable** as real substrate evidence. It does **not** invalidate already-closed PRODUCT-0 proofs or the locally sealed Windows mandatory program, because those rely on different proof surfaces and evidence chains.

## Findings accepted

The following audit findings are accepted and should be treated as kill findings against the Notebook packet:

- 30 supplied files collapse to 14 unique nonempty contents, with duplicates and two empty artifacts; duplicate volume is not corroboration.
- The original PostgreSQL evidence is internally incompatible: fixture `execution_id` values do not match the original UUID schema.
- The supplied mutation digests include fixture hashes (empty input and `Hello World!`) and therefore do not demonstrate canonical target-row commitments.
- The original RLS attack transcript lacks a positive control and includes malformed hostile SQL; it cannot prove RLS isolation.
- Kubernetes and cloud result files are simulator assertions unless backed by native cluster/provider sessions, target counts, and independently captured observations.
- The supplied Nitro fixture is structurally inconsistent with a real attestation proof: no raw COSE attestation object, certificate chain, freshness proof, verifier output, or separate SEV-SNP evidence is present.
- Provider-native idempotency must be treated operation-by-operation; there is no universal cloud `ClientRequestToken`/`requestId` contract.
- Cloud audit logs are corroborating evidence, not universal consequence finality.
- PostgreSQL RLS is not an immutability boundary against owners, superusers, `BYPASSRLS`, storage administrators, or a trusted writer allowed to fabricate receipt fields.

## Important audit limitation

Where the audit says repository objects or named commits were unavailable, read that as **packet-scoped**. The auditor did not have connected GitHub repository access. Current repository truth is established separately through GitHub and must override any packet-level `UNVERIFIED ASSERTION` classification when the referenced object can actually be resolved.

At the time of this reconciliation, canonical `main` includes the semantic-isolation merge at commit:

```text
609b6c7382c838a670db513dd9d02aaa9be08a93
```

Do not import Notebook-generated fake/synthetic commit/tree/seal metadata into repository truth.

## PostgreSQL correction accepted

Use `postgres_e3_rls_schema_v2.sql` from this handoff branch as the preferred clean-install design over the earlier corrected schema.

Key properties:

- dedicated `veklom_e3` schema;
- transition-centric consequence identity;
- distinct tenant login principals mapped through protected `session_user` binding;
- worker role is SELECT-only;
- target adapter is INSERT+SELECT only;
- E3 observer is SELECT-only;
- RLS enabled and forced;
- SECURITY DEFINER functions are schema-qualified with controlled `search_path`;
- business mutation and consequence receipt are required to commit in one target transaction;
- schema explicitly does **not** claim to prove CAPPO authorization, digest correctness, atomicity, or resistance to privileged DB administration.

This remains `DESIGN_VALID / REQUIRES REAL POSTGRESQL RERUN`.

## Strongest product-level discovery retained

The audit's strongest architectural observation is worth keeping:

> Veklom's defensible mechanism is not an "8/8 attack certification." It is a transition-centric evidence compiler for governed consequences: preserve authorization, exact consequence attempt, native/target observation, ambiguity state, reconciliation, authority disposal, and durable evidence as distinct facts.

This is compatible with the existing Veklom doctrine as long as "evidence compiler" is treated as a product/mechanism description, not a replacement for CAPPO, CapabilityLease, VRE, or PGL boundaries.

## Rerun discipline

Use `consequence_evidence_rerun_spec_v2.json` as a **falsification specification**, not as new execution evidence.

High-value additions from the audit that Antigravity should preserve when implementing the K8s/Postgres/Cloud/S5 programs:

- every denial needs a positive control;
- every consequence needs before/after target state and exact effect count;
- crash-after-commit must be deterministic and occur before acknowledgement;
- independent observer credentials/path/provenance must be separately attributable;
- canonical commitments must be recomputable from captured bytes;
- role-bypass assumptions must be measured, not assumed;
- negative networking tests need deny + allowed control + recovery and native attachment evidence;
- provider idempotency and finality rules must be exact-operation profiles;
- Nitro and SEV-SNP remain separate verifier profiles.

Do **not** let this rerun spec reopen PRODUCT-0 A-L or Windows WNB milestones that are already closed at their recorded proof surfaces. Apply the new controls only where they are relevant to future substrate implementations or repository-canonical reruns.

## Current corrected verdict

```text
Notebook master certification packet      INVALID AS CERTIFICATION
Notebook architecture / attack design     USEFUL FORWARD-SCOUT INPUT
PostgreSQL schema v2                       DESIGN_VALID / RERUN REQUIRED
Rerun specification v2                    VALID SPECIFICATION / NOT EVIDENCE
Kubernetes/PostgreSQL substrate claim      NOT YET SEALED
Cloud control-plane substrate claim        NOT YET MEASURED
S5 confidential-compute claim              NOT YET MEASURED / REAL TEE REQUIRED
Windows mandatory local proof program      UNAFFECTED BY THIS PACKET AUDIT
PRODUCT-0 A-L                              UNAFFECTED / REMAINS CLOSED
```

## Antigravity action

1. Finish Windows repository canonicalization first.
2. Then consume this audit correction with the existing K8s/Postgres forward-scout package.
3. Prefer schema v2 over the older corrected PostgreSQL schema for a clean install.
4. Treat `consequence_evidence_rerun_spec_v2.json` as the expanded hostile-test/control matrix.
5. Do not promote any K8s/Postgres/Cloud/S5 claim until a real run produces raw attributable evidence and exact source SHA.
