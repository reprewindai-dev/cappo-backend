# N8N-16 Correlated Execution Trace — QUARANTINED

**Status:** `UNVERIFIED_CLAIM_SET`

The prior version of this document presented a timestamped correlated trace as observed runtime fact and included concrete execution, policy, key, transaction, receipt, and evidence-hash identifiers together with ACTIVE, signature-valid, mutation-successful, settlement-successful, PGL-recorded, and synchronized-state claims.

Those values are not accepted as canonical evidence unless they are independently reproducible from the authoritative runtime and durably correlated to source-controlled code plus Gnomledger/PGL evidence. A manually transcribed table is not sufficient proof of execution, payment, cryptographic verification, persistence, or convergence.

## Verification boundary

`observed_current_responsibilities = CAPPO authority/governance and execution-dispatch code present in source`

`target_responsibilities = governed consequence execution with durable evidence and independently verifiable correlation`

`reported_runtime_state = CAPPO:8002`

`verified_runtime_state = NOT_VERIFIED`

`unverified_claims = [correlated N8N-16 execution, KMS/HSM signature verification, x402 reservation/settlement, external mutation success, PGL/Gnomledger evidence persistence, final cAPI synchronization]`

## Required replacement evidence

A replacement trace must be machine-produced from the tested runtime, bind each event to the deployed source/image identity, prove the canonical listener and protocol identity, preserve replay-safe authority correlation, and reference durable Gnomledger/PGL records that can be checked independently. Simulated or mock components must be labeled as such and may not be promoted to VERIFIED/ACTIVE production state.
