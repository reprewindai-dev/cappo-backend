# Authority is task-bound, never time-bound

Owner's rule, 2026-10-10: **nobody holds authority for longer than their task. A grant is not a time
window. It is the duration of one task. The moment the task's consequence is committed, the
authority is over.**

This document is the binding clause. Code, tests, lab fixtures and future designs (V-Chip intake,
VirtualDB, Private Cloud jobs, drones, robots, software agents) conform to it or are wrong.

## The rule, mechanically

1. **One grant, one operation, once.** A capability mount is issued for one exact operation on one
   resource and one target. The first ALLOW consumes it (`nonce_consumed = True` in
   `cappo_backend/capability_mount/service.py`). Every later use, same operation or a new one, is
   refused (`token_replay`), and a receipt-row guard refuses it even if the consumed flag is rolled
   back (`token_replay_receipt`).
2. **The clock is a backstop, not the end.** `ttl_seconds` (default 300, `MAX_TTL_SECONDS = 600`)
   exists only so that a grant that is never used cannot sit on authority. A used grant is closed by
   its task, and a wall-clock expiry must never be the thing that ends it.
3. **Out of scope is refused, never widened.** An action outside the bound operation is a
   `lease_invariant_violation`: nothing runs, the refusal is recorded with its reason.
4. **Revocation is immediate.** `terminate` closes the grant at once; a terminated grant is refused
   (`terminated`) regardless of remaining time.
5. **No pre-issued windows.** Authority is never issued for a span of epochs or a span of time in
   which many tasks may happen. A lease that would stay ACTIVE after its task's receipt commits is a
   spec violation, wherever it lives.
6. **Frontiers chain.** A state frontier carries the previous state root as `prev_root_hash`. Only a
   genesis frontier may carry the zero root, and it must be marked genesis.

## Order of checks on execute (production)

terminated → expired → consumed or token mismatch → prior receipt exists → policy evaluation →
single consequence → receipt → ledger anchor. A deny at any step is terminal for that attempt.

## What the lab taught

`Witness_Root.json` in the V-Chip hostile lab (2026-09-24) carried a ghost lease with an epoch window
[100, 200], a December expiry and status ACTIVE after its task committed at epoch 100, plus a zero
`prev_root_hash`. That is authority outliving its task. `verify_task_bound_lease.py` (kept with the
evidence) rejects it and accepts the same witness once the lease is closed at the task's commit.
When V-Chip semantics are brought into CAPPO (P0 source intake), leases close on the terminal
receipt and frontiers chain. No epoch-window leases.

## Open design choice (owner decides)

Today an out-of-scope attempt is refused and the grant stays usable for its bound operation. The
stricter rule, "one out-of-scope attempt terminates the grant", is a small change in the execute
path (terminate on `lease_invariant_violation`) with tests, and it changes the recorded demo flow
(after a blocked attempt the agent would have to ask for a new grant). Not implemented until the
owner chooses it.
