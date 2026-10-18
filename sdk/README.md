# Veklom CAPPO Client SDKs

Status: **PROVISIONAL CONTRACT-ALIGNED STUBS**

These SDKs preserve the current PRODUCT-0 response models and typed error mappings, but they are **not yet claimed as mechanically generated from OpenAPI**. The current Notebook contract draft does not fully define request-body and security schemas for all operations, so generated-client equivalence is not yet proven.

## Languages
- Python: `sdk/python/cappo_client.py`
- TypeScript / Node.js: `sdk/typescript/cappoClient.ts`

## Error mappings
- 401 / 403 -> `AuthorityDeniedError`
- 422 + `ExecutionIdMismatchError` -> `ExecutionIdMismatchError`
- 422 + `EnvelopeSubstitutionError` -> `EnvelopeSubstitutionError`
- 423 + `ReplayDeniedError` -> `ReplayDeniedError`
- 423 + `RetryLockedError` -> `RetryLockedError`
- 423 + `AuthorityLockedError` -> `AuthorityLockedError`
- 503 -> `InfrastructureUnavailableError`

## Authority custody
The clients never send or expose `Veklom-Authority`. For machine-to-machine calls, provide a scoped bearer token issued through the canonical server-side authority path.

## Python
```python
from sdk.python.cappo_client import CappoClient, RetryLockedError

client = CappoClient("http://127.0.0.1:8002", bearer_token="<scoped-test-or-m2m-token>")
mount = client.mount_authority("cap_invoice_payout", {
    "memory_max_bytes": 67108864,
    "compute_fuel_units": 50000000,
    "wall_deadline_ms": 300,
    "allow_network": True,
})

try:
    result = client.dispatch_consequence(
        mount.lease_id,
        mount.execution_id,
        mount.envelope_digest,
        {"vendor_id": "V-102", "amount_cents": 500000},
    )
except RetryLockedError:
    print("Execution remains fail-closed in OUTCOME_UNKNOWN.")
```

## TypeScript
```ts
const client = new CappoClient('http://127.0.0.1:8002', '<scoped-test-or-m2m-token>');
```

## Promotion criterion
Only change the status to "generated from canonical OpenAPI" after:
1. canonical OpenAPI requestBody + security schemes are complete;
2. code generation is run from that exact commit;
3. generated SDK contract tests pass against the same OpenAPI;
4. real CAPPO port-8002 integration tests pass.
