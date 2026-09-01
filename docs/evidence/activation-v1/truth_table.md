# Activation v1 truth table

Source commit: `fcff23d762d89e521dca0114508570b3deee47f2`
Harness: `activation-v1-harness-2026-09-01.r2`

| Step | Requested action | Identity | Decision | Reason | Invocation delta | On-disk effect | Evidence records | Termination | Result |
|---|---|---|---|---|---:|---|---|---|---|
| 1 | Mount `activation@v1` | JWT workspace `workspace-activation-v1`; mTLS `spiffe://example.org/workload/cappo-backend` | allow | mounted | 0 | No effect requested | Biscuit present in response and persisted `token_json`; matching SHA-256 and length | live before execution | PASS |
| 2 | Full server restart | Same configured Biscuit root; new process | — | — | 0 | No effect | Initial/restart server process logs; same SQLite path and root source | mount remains live | PASS |
| 3 | `record.read` on persisted mount | Same authenticated workspace and mTLS identity | allow | allowed | 1 | Target absent; consequence failed after invocation | Authority metadata and durable consequence events | terminated | PASS |
| 4 | `record.create` / `allowed-record` | Same authenticated workspace and mTLS identity | allow | allowed | 1 | Record created with expected content | `AUTHORIZED → STARTED → SUCCEEDED`; receipt/evidence state | terminated | PASS |
| 5 | Identical replay of step 4 | Same identity and operation intent | deny | `idempotency_replay:succeeded` | 0 | Record unchanged | Replay response; counter unchanged | remains terminated | PASS |
| 6 | `record.delete` / `protected-record` | Same authenticated workspace and mTLS identity | deny | `blocked_action` | 0 | Protected file byte-identical | CAPPO action-decision denial evidence | terminated | PASS |
| 7 | Identical replay of step 6 | Same identity and request | deny | `execution is terminated` | 0 | Protected file unchanged | Replay response | terminated | PASS |
| 8 | `record.create` with a cryptographically valid Biscuit granting only `record.read`, while mount profile/lease allows create | Same authenticated workspace and mTLS identity | deny | `lease_invariant_violation` | 0 | No record created | Biscuit extracted successfully; captured action-decision response/state | terminated | PASS |

Step 6 is a genuine CAPPO policy denial: `blocked_action` is the canonical
block-precedence branch. Step 8 is the separate post-verification authority
denial. It is neither `missing_cryptographic_authority`, `blocked_action`, nor
`not_in_capability_profile`.

The Docker-host result is not inferred from this direct-host run and remains
`LOCAL-HOST RUNTIME VERIFICATION REQUIRED`.
