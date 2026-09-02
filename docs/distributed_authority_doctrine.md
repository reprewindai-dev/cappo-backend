# Veklom Distributed Authority Doctrine

This doctrine specifies the strict invariants and engineering protocols for cryptographic authority recovery, evidence sealing, and consequence execution within the Capability OS infrastructure.

## 1. PGL Anchoring & Database Restoration

**Invariant Update**: Asynchronous Proof-of-Graph Ledger (PGL) anchoring does **not** close the full-DB-restore vulnerability window on its own. 
Because PGL writes may lag behind the immediate execution of a capability, a full database restoration to a point-in-time snapshot could rewind both the nonce and the local receipt table before the asynchronous PGL anchor completes.

**Exact Recovery Invariant**: 
> "An `authority_generation` (cryptographic key rotation/issuance) witnessed externally cannot be re-executed after restore."

If the database is restored to `t-1`, any authority generation keys minted at `t=0` must be immediately invalidated. External witnessing of the capability (or its receipt) acts as the tie-breaker.

## 2. External Rollback Witness

To enforce the Recovery Invariant, we introduce the **External Rollback Witness**.

### Ordering of Engineering Steps
1. **External Rollback Witness (First)**: Establish the cryptographic receipt and authority generation witness before updating internal state models.
2. **Recovery State Machine (Second)**: Update the Consequence State Machine (`ConsequenceState` and `_ALLOWED_TRANSITIONS`) only after the external witness model is defined and proven.

### 3. Consequence Evidence Separation

`ConsequenceEvidence` MUST be separated from the transient `CapabilityActionReceipt`. 
- `CapabilityActionReceipt` is an ephemeral ticket representing the right to execute.
- `ConsequenceEvidence` is the non-repudiable cryptographic proof that the capability *was* executed and its effects were observed by the system, anchored via PGL and the Rollback Witness.
