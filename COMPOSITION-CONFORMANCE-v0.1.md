# VEKLOM COMPOSITION CONFORMANCE v0.1

**Date:** 2026-08-30
**Status:** ACTIVE SPECIFICATION

This document defines the architectural rules for the **Governed Composition Chain**. As Veklom shifts from foundational proving to multi-hop orchestration and product activation, this conformance specification governs how nodes connect.

## 1. The Composition Invariant

**Composition must not create authority.**

Mathematically expressed:
$$A_{n+1} \subseteq A_n$$
or
$$Authority(A \rightarrow B \rightarrow C) \subseteq Authority(A) \cap Authority(B) \cap Authority(C)$$

Every transition across an interstitial boundary can preserve or attenuate authority, but it must never implicitly widen it.

## 2. Governed Composition Chain Test Requirements

To be considered a conforming Governed Composition Chain, any multi-hop execution flow must demonstrate and test the following properties:

1. **Identity Continuity:** The cryptographic identity (Execution Identity, EI) of the initiating actor must remain mathematically unbroken across all hops.
2. **Authority Attenuation:** At each handoff, the receiving node must explicitly enforce that its granted authority does not exceed the authority of the sending node.
3. **Constraint Continuity:** Budgetary, temporal (leases), and geographic (PII/Law 25) constraints must propagate synchronously with the payload.
4. **Resource Equivalence:** The resource consumed by a composition must map equitably back to the authorized limits of the originating tenant.
5. **Consequence Singularity:** A single intended action must yield exactly one verifiable consequence; side-effects must be tracked or explicitly forbidden.
6. **Epistemic Discipline:** Retrieval finds claims. Governance determines authority. Evidence establishes what may safely be treated as fact. AI reasons only *after* this discipline is applied.
7. **Evidence Continuity:** The cryptographic receipt (Proof-of-Graph) must contain the full unbroken chain of custody from origin to execution.
8. **Explicit Uncertainty:** Any hop that loses confidence in the state of its predecessor or successor must halt and emit `OUTCOME_UNKNOWN` rather than guessing.
