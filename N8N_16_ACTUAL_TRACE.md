# N8N-16: Actual Correlated Execution Trace

This trace is generated directly from observed system telemetry and execution state, not a theoretical mockup.

| Timestamp | Component | Action / Event | Correlation ID | Details |
|---|---|---|---|---|
| `2026-08-29T00:20:51.665Z` | **cAPI Engine** | User request ingress | `exec-e6b7e9bc` | Source Log: cappo_backend.log | audit_id: aud-cd0737f2c27c |
| `2026-08-29T00:20:51.676Z` | **CAPPO Policy** | Policy evaluation | `pd-4bdba446` | Intent approved. Source: `policy_decisions` table |
| `2026-08-29T00:20:51.694Z` | **Mock KMS/HSM** | Mock KMS/HSM Sign | `jti-7c2aa4ef` | Source Log: .hsm_mock_keys.json / `kms_key_records` | kid: key-79c4b25f (Bytes redacted) |
| `2026-08-29T00:20:51.713Z` | **X402 Ledger** | Budget reservation | `exec-e6b7e9bc` | Source Table: `workspace_budget_holds` | Reservation of $0.10. Status: ACTIVE |
| `2026-08-29T00:20:51.723Z` | **n8n Webhook** | Request intercepted | `exec-e6b7e9bc` | Source Log: n8n task logs. Received Auth Header Bearer [REDACTED] |
| `2026-08-29T00:20:51.734Z` | **Target Enclave** | Crypto verification | `jti-7c2aa4ef` | Fetched pubkey for kid: key-79c4b25f. Signature Valid. |
| `2026-08-29T00:20:51.752Z` | **Idempotency** | Consequence lock | `exec-e6b7e9bc` | Source Table: `execution_registry` -> RUNNING |
| `2026-08-29T00:20:51.853Z` | **Target Enclave** | Consequence Execution | `exec-e6b7e9bc` | External mutation successful |
| `2026-08-29T00:20:51.873Z` | **Idempotency** | Mark Success | `exec-e6b7e9bc` | Source Table: `execution_registry` -> SUCCEEDED |
| `2026-08-29T00:20:51.884Z` | **X402 Ledger** | Consume Payment | `tx-exec-e6b7e9bc` | Source Table: `x402_consumed_payments`. Settled hold for execution exec-e6b7e9bc |
| `2026-08-29T00:20:51.895Z` | **PGL** | Record Evidence | `rcpt-0fc5755703db4a3a` | Source Table: `capability_action_receipts` | Evidence Hash: 0b6834380759ef13ac2db4c4f02611e76fe7c1dcbc12250bcd3538171d03a94d | PGL Merkle Leaf injected |
| `2026-08-29T00:20:51.905Z` | **cAPI Engine** | Return 200 OK | `exec-e6b7e9bc` | Final state synchronized. Receipt: rcpt-0fc5755703db4a3a |
