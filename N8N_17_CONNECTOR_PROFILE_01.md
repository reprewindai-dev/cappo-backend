# N8N-17 Connector Certification Profile 01

**Connector:** `sandbox_file_append`  
**Evidence state:** `VERIFIED_REPO`  
**Live state:** `UNVERIFIED` until CAPPO, PostgreSQL, target worker, and the n8n workflow are running together

## Capability boundary

- Action: `fs:append`
- Compensation action: `fs:append:compensate`
- Audience: `sandbox_file_append`
- Resource: the exact configured `sandbox-file:<normalized-path>` value
- Default local target: `/tmp/n8n_governed_append.log`
- Wildcard resources are rejected.
- Content is limited to 4096 UTF-8 bytes.
- Symlink, hard-link, path-parent drift, and non-regular-file targets are denied.

## Authority and isolation

The target accepts a compact JWT in the HTTP `Authorization: Bearer` header. It fetches only the Ed25519 public key identified by `kid` from CAPPO's `/api/v1/execution/keys/{kid}` route. The target verifies issuer, audience, signature, temporal claims, required execution claims, live revocation state, allowed action, and exact resource scope. n8n cannot mint authority.

## Idempotency and evidence

- `execution_id` and `jti` are reserved before the consequence.
- Reusing either identity with different action data is denied.
- A completed duplicate returns the original receipt without another append.
- The connector writes one canonical JSONL operation record using append mode and `fsync`.
- Positive reconciliation scans the complete structured operation log and verifies the action hash.
- Missing evidence is treated as unknown, not proof that the action failed.

Each receipt contains the connector ID, execution ID, operation ID, normalized resource, action hash, record hash, byte count, timestamp, and optional compensated execution ID.

## Compensation

Compensation is a separate governed execution requiring `fs:append:compensate`. It appends a tombstone referencing the original execution. It does not use `sed`, rewrite the log, erase evidence, or refund the original execution automatically.

## Repository verification

The certification suite covers:

1. Exact-scope append success.
2. Duplicate delivery returning the original receipt.
3. Altered action data under the same execution ID being denied.
4. Wildcard/wrong resource denial.
5. Append-only governed compensation.
6. HTTP worker JWT verification and receipt propagation.

Verification performed:

```text
pytest tests/test_n8n_17_sandbox_file_connector.py: 6 passed
ruff on connector, key route, target worker, and tests: passed
CAPPO OpenAPI key-discovery route registration: passed
```

## Remaining live release gate

Run the real boundary:

```text
CAPPO :8002 -> n8n :5678 -> target :8099 -> PostgreSQL :5432
```

Capture a redacted correlated trace proving key fetch, signature verification, live revocation check, one physical append under concurrent duplicate delivery, original receipt replay, budget settlement, and append-only compensation. Until that evidence exists, this connector is `VERIFIED_REPO`, not `VERIFIED_LIVE`.
