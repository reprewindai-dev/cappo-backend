# VEKLOM WID-1 SCHEMA DOCTRINE

This document sets the definitive baseline for Veklom Workload Identity (WID-1).
It defines the ontological limits of identity verification inside CAPPO and PGL.

## Canonical Definitions

- **Workload Identifier**: A URI-like pattern describing what kind of machine is allowed to act (\wimse://<trust-domain>/<environment>/<system>/<role>/<workload>\).
- **Persistent Profile**: Describes what that machine may normally do (e.g., default rights, limits).
- **Ephemeral Execution Identity**: Proves this specific execution instance currently exists (short-lived, cryptographically bound).
- **Authority Artifact**: Proves this specific consequence is allowed (binds policy decision to execution).
- **Workload Proof Token (WPT)**: Proves this exact request came from the valid holder of the identity.

## Limitations and Enforcement Boundaries

- **WID-1 does not claim runtime enforcement.** This doctrine is schema, fixture, and validation discipline only. WID-2 provides enforcement.
- **WID-1 does not claim WIMSE conformance.** It is WIMSE-aligned but uses tailored properties for strict execution consequences.
- **WID-1 only proves schema validity and negative-fixture rejection.**
