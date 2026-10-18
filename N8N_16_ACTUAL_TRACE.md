# N8N-16 Correlated Execution Trace — QUARANTINED

**Status:** `UNVERIFIED_CLAIM_SET`

This document presents a timestamped correlated trace together with concrete execution, lease, ledger, audit, receipt, and evidence-hash identifiers and authorized, signed, reconciled, settled, PGL-anchored, and verified claims.

Those values are not accepted as canonical evidence unless they are independently reproducible from the authoritative runtime and durably correlated to source-controlled code plus Gnomledger/PGL evidence. A manually transcribed table is not sufficient proof of execution, payment, cryptographic verification, persistence, or convergence.

## Verification boundary

`observed_current_responsibilities = CAPPO authority/governance and execution-dispatch code present in source`

`target_responsibilities = governed consequence execution with durable evidence and independently verifiable correlation`

`reported_runtime_state = CAPPO:8002`

`verified_runtime_state = NOT_VERIFIED`

`unverified_claims = [correlated N8N-16 execution, KMS lease signing, local budget hold/settlement, n8n webhook dispatch, physical consequence reconciliation, CAPPO append-only events and hash-chained audit, PGL/Gnomledger receipt anchoring]`

## Required replacement evidence

A replacement trace must be machine-produced from the tested runtime, bind each event to the deployed source/image identity, prove the canonical listener and protocol identity, preserve replay-safe authority correlation, and reference durable Gnomledger/PGL records that can be checked independently. Simulated or mock components must be labeled as such and may not be promoted to VERIFIED/ACTIVE production state.

## Quarantined claim set (as reported, unverified)

Reported (self-attested) evidence state: `VERIFIED_LOCAL`. The author reported this as generated from a live local run, PostgreSQL rows, and the reconciled physical sandbox record. It is retained below as an unverified claim set, not as canonical evidence.

| Timestamp | Component | Event | Correlation | Reported detail (unverified) |
|---|---|---|---|---|
| `2026-08-29T01:34:17.103152Z` | UI / certification client | Intent created | `exec-live-34909298f11c` | Real local certification intent |
| `2026-08-29T01:34:17.199072Z` | CAPPO / PostgreSQL | Budget hold + authority event committed | `exec-live-34909298f11c` | 1 cent ACTIVE hold; AUTHORIZED event v0 |
| `2026-08-29T01:34:17.209492Z` | CAPPO KMS | Lease signed | `lease-live-e266d0836d4a` | Ed25519 JWT created; token bytes not logged |
| `2026-08-29T01:34:17.209492Z` | CAPPO | Dispatch started | `exec-live-34909298f11c` | STARTED event v1 committed before webhook call |
| `2026-08-29T01:34:18.174509Z` | n8n | Webhook returned | `exec-live-34909298f11c` | HTTP 200 |
| `2026-08-29T01:34:18.175506Z` | Target enclave | Physical consequence reconciled | `exec-live-34909298f11c` | record_hash=56c3f587299dd5d4a07ff86d82f0ca89cd3b6a5a7ada3c49bb91cef7c6ee27a2 |
| `2026-08-29T01:34:18.201503Z` | CAPPO / PostgreSQL | Consequence + settlement committed | `exec-live-34909298f11c` | SUCCEEDED event v2; local-ledger settlement; hash-chained audit |

| `2026-08-29T05:07:33.600326Z` | Gnomledger/PGL | Consequence Receipt Anchored | `exec-live-34909298f11c` | `event_hash=51cc546fc2fefec569f268e12d8523b19f238cd1677b44eb25514591dcefaa01` |

## Reported release-gate result (unverified)

```json
{
  "audit_hash": "13abf6245295339a1f8088adaad4e04db03557918c9de2a1db8383d8cd4d90d5",
  "budget_balance_after_cents": 99,
  "budget_balance_before_cents": 100,
  "execution_id": "exec-live-34909298f11c",
  "pgl_receipt_verified": true,
  "pgl_verification_substrate": "local Gnomledger",
  "pgl_persistence": "durable local database",
  "external_remote_pgl_verified": false,
  "pgl_event_hash": "51cc546fc2fefec569f268e12d8523b19f238cd1677b44eb25514591dcefaa01",
  "lease_id": "lease-live-e266d0836d4a",
  "local_ledger_id": "local-ledger:a8b4fbe3aa8f7308ee20c8adf49eb1392dd31ebdab8b190c9e8e6bec7f609d7a",
  "onchain_x402_verified": false,
  "physical_action_hash": "79a2696112fce0ec36ac0188060bd596fb7a8b9aee3632cd258ba9bf8d6d5ae9",
  "physical_record_hash": "56c3f587299dd5d4a07ff86d82f0ca89cd3b6a5a7ada3c49bb91cef7c6ee27a2",
  "settlement_amount_cents": 1,
  "webhook_error": null
}
```

## Reported claim boundary (unverified)

- Exactly-once **local** budget settlement: reported, not independently verified.
- CAPPO append-only consequence events: reported, not independently verified.
- CAPPO hash-chained audit receipt: reported, not independently verified.
- PGL receipt verified: reported **true**, not independently verified
- PGL verification substrate: local Gnomledger
- PGL persistence: durable local database
- External/remote PGL verified: **false**
- On-chain x402/USDC transaction: **UNVERIFIED** (no chain transaction submitted).
