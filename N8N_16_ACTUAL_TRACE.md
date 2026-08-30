# N8N-16 Actual Correlated Execution Trace

Evidence state: **VERIFIED_LOCAL**. Generated from a live local run, PostgreSQL rows, and the reconciled physical sandbox record.

| Timestamp | Component | Event | Correlation | Observed detail |
|---|---|---|---|---|
| `2026-08-29T01:34:17.103152Z` | UI / certification client | Intent created | `exec-live-34909298f11c` | Real local certification intent |
| `2026-08-29T01:34:17.199072Z` | CAPPO / PostgreSQL | Budget hold + authority event committed | `exec-live-34909298f11c` | 1 cent ACTIVE hold; AUTHORIZED event v0 |
| `2026-08-29T01:34:17.209492Z` | CAPPO KMS | Lease signed | `lease-live-e266d0836d4a` | Ed25519 JWT created; token bytes not logged |
| `2026-08-29T01:34:17.209492Z` | CAPPO | Dispatch started | `exec-live-34909298f11c` | STARTED event v1 committed before webhook call |
| `2026-08-29T01:34:18.174509Z` | n8n | Webhook returned | `exec-live-34909298f11c` | HTTP 200 |
| `2026-08-29T01:34:18.175506Z` | Target enclave | Physical consequence reconciled | `exec-live-34909298f11c` | record_hash=56c3f587299dd5d4a07ff86d82f0ca89cd3b6a5a7ada3c49bb91cef7c6ee27a2 |
| `2026-08-29T01:34:18.201503Z` | CAPPO / PostgreSQL | Consequence + settlement committed | `exec-live-34909298f11c` | SUCCEEDED event v2; local-ledger settlement; hash-chained audit |

| `2026-08-29T05:07:33.600326Z` | Gnomledger/PGL | Consequence Receipt Anchored | `exec-live-34909298f11c` | `event_hash=51cc546fc2fefec569f268e12d8523b19f238cd1677b44eb25514591dcefaa01` |

## Release-gate result

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

## Claim boundary

- Exactly-once **local** budget settlement: verified.
- CAPPO append-only consequence events: verified.
- CAPPO hash-chained audit receipt: verified.
- PGL receipt verified: **true**
- PGL verification substrate: local Gnomledger
- PGL persistence: durable local database
- External/remote PGL verified: **false**
- On-chain x402/USDC transaction: **UNVERIFIED** (no chain transaction submitted).


