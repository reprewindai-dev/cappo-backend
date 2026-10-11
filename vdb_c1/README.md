# VDB-C1.0 — one governed physical consequence, finalized once

The normative contract is in [`PROTOCOL.md`](PROTOCOL.md). This file covers
how to run it, what was shown, and what was not.

**Provenance.** Built as a separate package, as instructed. The C0 freeze
checkpoint did not run, because the sealed C0 commit (`bc096962…`) is not in
any repository this work could reach. This package therefore claims no
C0 conformance and no continuity with C0 artifacts.

## Run

```bash
python -m vdb_c1.cli keygen --out authority.key
python -m vdb_c1.cli init --home H --namespace ns:demo --authority-key authority.key
python -m vdb_c1.cli sign-intent --key authority.key --home H --set alpha 1 --out intent.json
python -m vdb_c1.cli submit --home H --intent intent.json      # add --crash-at POINT to kill it
python -m vdb_c1.cli export --home H --intent-id <id> --out bundle.json
python vdb_c1/verify.py bundle.json H/trust_store.json          # separate process, public keys only
python -m vdb_c1.prove --out vdb_c1/evidence/<run_id>           # recorded proof run
pytest tests/test_vdb_c1_states.py tests/test_vdb_c1_consequence.py tests/test_vdb_c1_evidence.py
```

Dependencies are `cryptography` and `rfc8785` (both already in cappo-backend)
and Python's `sqlite3`. No root or network is needed.

## Files

| File | Role |
|---|---|
| `states.py` | normative state machine |
| `canon.py` | JCS (`rfc8785`), digests, Ed25519, state roots |
| `journal.py` | append-only, hash-chained, fsynced protocol journal (separate from state) |
| `state_adapter.py` | the one physical adapter: SQLite WAL, `synchronous=FULL`, one conditional UPSERT |
| `coordinator.py` | the one trusted finalizer: admission, preconditions, ordered execution, crash points, recovery, bundle export |
| `cli.py` | command line |
| `verify.py` | offline verifier; imports nothing from this package |
| `prove.py` | recorded proof run |
| `evidence/20261011T044036Z/` | committed run: two bundles, trust store, clean-room verdicts, manifest |

## What was shown (this container: Linux 6.18, Python 3.13.16, SQLite 3.45.1)

`pytest`: 60 tests pass. That is 16 state-machine tests, 40 consequence
tests and 4 evidence tests. Crashes are real process deaths: `os._exit`
in a subprocess at the named point, with nothing flushed that was not
already fsynced.

| Case | Result |
|---|---|
| Valid intent | `FINALIZED`; generation +1; bundle verifies `VALID_FINALIZED` in a separate process, in an empty directory holding only `verify.py`, the bundle and `trust_store.json` |
| 11 invalid admissions (bad signature, untrusted key, expired, not yet valid, wrong namespace, wrong op, extra member, float, kid mismatch, TTL too long, malformed) | `ADMISSION_REJECTED`, journaled; `execute_calls = 0`, `commit_calls = 0`, state unchanged |
| Stale generation / wrong root | `PRECONDITION_REJECTED`; no calls; state unchanged |
| Same `intent_id`, different bytes | `E_INTENT_CONFLICT`; no calls |
| A: crash after PREPARED | store untouched; recovery → `ABORTED`; retry returns `ABORTED`, never executes |
| B: crash inside the open transaction | SQLite rolled back; recovery → `COMMIT_UNCERTAIN` → `ABORTED` |
| C: crash after durable COMMIT | store holds the effect; the retry → `COMMIT_UNCERTAIN` → `COMMITTED (recovered)` → `FINALIZED`; `execute_calls = 0`; bundle `VALID_FINALIZED` |
| C2: crash after the COMMITTED record | retry finalizes; bundle `VALID_FINALIZED` |
| D: finality durable, response lost | retry returns the stored certificate byte-for-byte; one FINALIZED record |
| 12 concurrent processes, same intent | one execution, 11 replays, one identical certificate, generation 1, one `applied` row |
| Committed state altered before confirmation | stays `COMMIT_UNCERTAIN`; no certificate; no export; no stacked records |
| Torn final journal line / damaged middle line | tail dropped / coordinator refuses to open |
| 12 tampered bundles | each rejected with its own verdict (journal edits, re-chained intent or state edits, generation skip, duplicate FINALIZED, certificate edits, unpinned keys, missing data, wrong receipt digest) |

Mutation check: I planted two bugs in the coordinator and the suite caught
each. One finalized despite a fresh-read mismatch; the other aborted
durably committed work during recovery. The first was caught only by the
fresh-read test, which is why that test exists.

The recorded run (`evidence/20261011T044036Z`):
- The first submit finalized normally.
- The second was killed at `after_storage_commit`: exit 77, no output, the
  store already at generation 2 with one `applied` row.
- The retry recovered it to `FINALIZED`.
- Both bundles verify `VALID_FINALIZED` in a clean room.
- `tests/test_vdb_c1_evidence.py` re-verifies both against keys pinned in
  the test.

## What this does not show

- **The finalizer is trusted.** One trusted finalizer is in scope. A
  finalizer that lies consistently in everything it journals and signs
  passes the verifier. Catching that needs an independent witness, which is
  the W1 profile and out of scope here.
- **Power loss was not tested.** The crashes are process kills, which
  exercise SQLite's transaction semantics and the protocol's recovery. They
  do not test whether the disk honours `fsync`; that needs a power-cut rig.
  The durability profile (`WAL`, `synchronous=FULL`) is read back on every
  connection and written into each certificate.
- **The in-transaction precondition re-check (→ `ABORTED`) has no test.**
  The finalizer lock serializes every submit, so nothing can race between
  the precondition read and `BEGIN`. That branch is defensive and was not
  exercised.
- **Bundles expose earlier intents.** A bundle carries the journal prefix
  from genesis, including other intents' keys and values. That suits one
  namespace in C1.0, not a multi-tenant store.
- **Wall-clock time.** Admission time and `admitted_at` come from the
  coordinator's clock. The verifier checks consistency, not true time.
- **Out of scope** (as specified): quorum, replication, sharding, arbitrary
  SQL, Parquet, multi-party finality.
