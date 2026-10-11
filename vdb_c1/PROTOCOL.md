# VDB-C1.0 — Physical Consequence Protocol (normative)

Status: **C1.0 reference slice.** This package was built as a separate
package, as instructed, without the VDB-C0 freeze checkpoint: the sealed C0
commit `bc096962…` is not in any repository this work could reach. Nothing
here claims C0 conformance or continuity with C0 artifacts.

## Scope

Exactly one of each:

- one namespace per state store;
- one operation: a deterministic conditional `UPSERT` of a string key to a
  string value;
- one trusted finalizer (one Ed25519 key), serialized by an exclusive lock;
- one append-only, hash-chained protocol journal, separate from state;
- one state-root format;
- one Ed25519 authority model;
- one offline-verifiable finality bundle.

Out of scope: quorum, arbitrary SQL, sharding, replication, multi-party
finality, Parquet.

## Objects

All hashed or signed objects are RFC 8785 (JCS) canonical JSON. Every
hashed object carries a `protocol` member, which domain-separates the hashes.
Digests are written `sha256:<lowercase hex>`. Signatures are Ed25519 over the
JCS bytes of `payload`, encoded base64url without padding:
`{"alg": "Ed25519", "kid": …, "payload": {…}, "sig": …}`. A key id is
`ed25519:` followed by the first 16 hex digits of SHA-256 over the raw public
key.

**MutationIntent** (`veklom.vdb.c1.intent/v1`), signed by an authority key.
It has exactly these members:
`protocol, intent_id, namespace, op ("UPSERT"), key, value,
expected_generation, expected_root, issued_at, expires_at, authority_kid`.
- `intent_hash = digest(intent payload)`.
- `payload_commitment = digest({"protocol": "veklom.vdb.c1.payload/v1",
  "op", "key", "value"})`.

**State root** (`veklom.vdb.c1.state/v1`):
`digest({"protocol", "namespace", "generation", "entries"})`, where
`entries` is every `[key, value]` pair of the namespace, sorted by key in
Unicode code-point order. The generation is part of the root, so the root
changes on every commit.

**Journal record** (`veklom.vdb.c1.journal/v1`):
`{protocol, seq, prev, at, kind, intent_id, body, hash}`.
- `hash = digest(record without hash)`.
- `prev` is the previous record's hash; it is `"genesis"` for `seq` 0.
- Each record is one line, written as JCS bytes plus `\n`, appended and
  fsynced before the step after it runs.

**FinalityCertificate** (`veklom.vdb.c1.finality/v1`), signed by the finalizer
key. It binds:
- `intent_id`, `intent_hash`, `namespace`, `authority_kid`;
- `expected_generation`, `pre_root`, `payload_commitment`,
  `post_generation`, `post_root`;
- `physical` (adapter, store id, durability settings, and the applied row);
- `journal` (seq and hash of the PREPARED and COMMITTED records);
- `finalized_at` (the COMMITTED record's time) and `finalizer_kid`.

Every field comes from the journal, so recovery re-derives a byte-identical
certificate (Ed25519 signatures are deterministic).

## State machine

```
RECEIVED ──> ADMISSION_REJECTED
RECEIVED ──> ADMITTED ──> PRECONDITION_REJECTED
                      └─> PREPARED ──> ABORTED
                                   └─> COMMITTING ──> FINALIZED
                                                  └─> COMMIT_UNCERTAIN ──> FINALIZED   (recovery)
                                                                       └─> ABORTED     (recovery)
```

- The two edges out of `COMMIT_UNCERTAIN` are recovery's resolution. They
  are taken only from the state store's durable record of the intent, never
  by re-executing the mutation.
- `RECEIVED` and `ADMITTED` exist only in memory.
- Rejections are journaled as evidence but do not bind the `intent_id`. A
  PREPARED record binds `intent_id` to `intent_hash` for good.
- A COMMITTED journal record is not a state. It records the confirmed
  storage outcome inside `COMMITTING` or `COMMIT_UNCERTAIN`, and only
  FINALIZED may follow it.

Meanings:

| State | Meaning |
|---|---|
| ADMISSION_REJECTED | Not authorized or invalid for admission. No executor or commit path runs. |
| PRECONDITION_REJECTED | Admissible, but `expected_generation`/`expected_root` does not match current state. |
| PREPARED | Intent and bindings are durably journaled; no physical work has started. |
| ABORTED | No durable consequence exists. |
| COMMITTING | The storage transaction has mutated and has not yet been proven committed. |
| COMMIT_UNCERTAIN | Recovery or verification has not established the durable outcome; retries must not re-execute. |
| FINALIZED | The only state that asserts a durable consequence, bound to a signed certificate. |

## Execution order (one submit, under the finalizer lock)

1. Recover any intent left without a terminal record.
2. If `intent_id` is already bound: if the `intent_hash` differs, refuse with
   `E_INTENT_CONFLICT`; otherwise return the stored terminal result. Nothing
   is re-executed.
3. Admission:
   - schema is exact, with no extra members and no floats;
   - `op == "UPSERT"`, the namespace matches, and `kid == authority_kid`;
   - the kid is trusted and the signature verifies;
   - `issued_at - skew ≤ now < expires_at`, and the TTL is at most the
     maximum.
   On failure: journal `ADMISSION_REJECTED`; nothing else runs.
4. Precondition: a fresh read of `generation` and `state root` must equal
   the expected values. On failure: journal `PRECONDITION_REJECTED`.
5. Journal `PREPARED`, with the signed intent, the admission binding and the
   pre-state entries. *(crash point A: `after_prepared`)*
6. `BEGIN IMMEDIATE` on the SQLite WAL state store
   (`synchronous=FULL`). Inside the transaction:
   - re-check generation and root; on mismatch, ROLLBACK and journal
     `ABORTED`;
   - run the conditional UPSERT;
   - bump the generation with `WHERE generation = expected`;
   - insert the `applied(intent_id …)` row (primary key: at most once).
7. Journal `COMMITTING`. *(crash point B: `before_storage_commit`, inside
   the open transaction)*
8. `COMMIT`. *(crash point C: `after_storage_commit`)*
9. A fresh connection reads the store and must confirm all of:
   - the `applied` row;
   - generation = expected + 1;
   - root(fresh entries) = the applied `post_root`;
   - fresh entries = pre-entries with the key set to the value.
   On any mismatch: journal `COMMIT_UNCERTAIN` and stop. No certificate.
10. Journal `COMMITTED`, with the post-entries and the applied row.
    *(crash point C2: `after_committed_record`)*
11. Sign the FinalityCertificate and journal `FINALIZED`.
    *(crash point D: `after_finalized`: the response is never sent)*

## Recovery

| Last record for an unterminated intent | Action |
|---|---|
| PREPARED | `applied` row absent → `ABORTED`; present → protocol violation (COMMITTING is journaled before COMMIT), coordinator stops |
| COMMITTING / COMMIT_UNCERTAIN | journal `COMMIT_UNCERTAIN` (once); `applied` present and the fresh read confirms → `COMMITTED` (`recovered: true`) → `FINALIZED`; absent → `ABORTED`; present but unconfirmed → stays `COMMIT_UNCERTAIN` |
| COMMITTED | `FINALIZED` (byte-identical certificate) |

A torn final journal line (one with no trailing newline) is dropped. Any
other journal inconsistency stops the coordinator.

## Invariants

```
InvalidAdmission ⇒ execution_calls = 0 ∧ commit_calls = 0 ∧ state_before = state_after
Finalized ⇒ Authorized ∧ PreconditionsMet ∧ AppliedAtMostOnce ∧ DurablyCommitted ∧ ReceiptBound
|{ FINALIZED records for intent_i }| ≤ 1
```

## Proof bundle and verdicts

The bundle (`veklom.vdb.c1.bundle/v1`) holds:
- the journal prefix from `seq 0` through the intent's FINALIZED record;
- the receipt: the signed certificate and its digest.

The verifier is `verify.py`. It runs in a separate process and gets only
the bundle and `trust_store.json`; it imports nothing from this package.
It returns exactly one verdict:

| Verdict | Meaning |
|---|---|
| `VALID_FINALIZED` | every check passed |
| `REJECTED_INCOMPLETE_BUNDLE` | a required object or member is missing or malformed |
| `REJECTED_JOURNAL_INCONSISTENT` | the hash chain, seq, prev, record order or state sequence is wrong |
| `REJECTED_UNTRUSTED_KEY` | an intent or certificate is signed by a kid not in the trust store |
| `REJECTED_BAD_SIGNATURE` | a signature does not verify |
| `REJECTED_OUTSIDE_AUTHORITY_WINDOW` | PREPARED was recorded outside the intent's validity window |
| `REJECTED_ROOT_MISMATCH` | a pre or post root does not recompute from its entries |
| `REJECTED_GENERATION_MISMATCH` | the generation chain is not expected → expected+1 |
| `REJECTED_PAYLOAD_SUBSTITUTION` | the committed state, commitment or certificate disagrees with the signed intent |
| `REJECTED_DUPLICATE_FINALITY` | the journal holds more than one FINALIZED or COMMITTED record for the intent, or conflicting bindings |
| `REJECTED_RECEIPT_MISMATCH` | the receipt, certificate and journal do not bind to one another |

What a bundle can and cannot show: the finalizer is trusted (one trusted
finalizer, by scope). A finalizer that lies consistently in every record
it signs and journals cannot be caught from the bundle. The bundle proves
internal consistency, authority, and binding to signed records; it does
not prove that the disk under SQLite honours fsync.

## W1: witnessed finality (`witness.py`)

A witness is a separate role with its own Ed25519 key and durable state
`{genesis_hash, seq, head_hash}`. On each observation it:

1. reads the journal file itself, read-only; a torn tail is a refusal, never
   a repair;
2. re-checks the full hash chain;
3. requires the same genesis, a journal at least as long as its last seq, and
   the record at its last seq still hashing to the head it signed. Otherwise
   it refuses (`E_DIFFERENT_JOURNAL`, `E_ROLLBACK`, `E_FORK`) and writes
   signed fork evidence;
4. runs the offline verifier on every new FINALIZED record; any failure is
   `E_INVALID_FINALITY` and nothing is signed;
5. signs a checkpoint (`veklom.vdb.c1.checkpoint/v1`):
   `{journal_genesis_hash, seq, head_hash, witnessed_at, witness_kid}`.

A W1 bundle adds `witness: {checkpoint, journal_suffix}`, where
`journal_suffix` holds the records after the intent's FINALIZED record up to
the checkpoint's head. The verifier then checks:
- the checkpoint is signed by a pinned `witness_keys` entry;
- the suffix chains on from the bundle's journal;
- the suffix holds no further record for this intent
  (otherwise `REJECTED_DUPLICATE_FINALITY`);
- the checkpoint's head is exactly the end of that history
  (otherwise `REJECTED_FORK`).

Verdicts carry `profile: L1 | W1`. With `--require-witness`, an L1 bundle
fails `REJECTED_NOT_WITNESSED`.

W1 makes history the witness has seen unforgeable after the fact. It does
not let the witness see the SQLite store.
