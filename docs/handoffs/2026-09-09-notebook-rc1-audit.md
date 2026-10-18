# Notebook PRODUCT-0 RC1 Audit

Date: 2026-09-09
Status: FALSIFIER REVIEW COMPLETE
Source classification: NOTEBOOK-REPORTED / SIMULATED HTTP HARNESS

## Executive verdict

Do **not** treat the Notebook `v0.9.0-PRODUCT-0-RC1` package as a repository-canonical release candidate or as proof of the real CAPPO live runtime yet.

Two independent falsifiers were found:

1. The GitHub tag `v0.9.0-PRODUCT-0-RC1` is not present in `reprewindai-dev/cappo-backend` at review time.
2. `scripts/run_hostile_port8002_battery.py` does not target an independently started CAPPO FastAPI/Uvicorn process. It starts its own in-process Python `HTTPServer` using a custom `BaseHTTPRequestHandler` that hard-codes the expected PRODUCT-0 responses and then sends requests back to that server.

Therefore the Notebook 10/10 result is **VALID as a self-contained HTTP contract simulation**, but **INVALID as evidence that the canonical CAPPO runtime enforced those outcomes**.

## Why the hostile battery is simulated, not canonical runtime proof

The supplied script:

- defines `CAPPOHostileServerHandler(BaseHTTPRequestHandler)` inside the battery itself;
- checks only whether `Authorization` begins with `Bearer `; the token is not cryptographically verified;
- hard-codes response branches for consumed lease, execution mismatch, envelope mismatch, revocation, OUTCOME_UNKNOWN lock, reconciliation unavailability, recovery, and valid dispatch;
- starts `HTTPServer(('127.0.0.1', 8002), CAPPOHostileServerHandler)` inside `run_hostile_battery_and_seal_evidence()`;
- then calls that same local server with `urllib.request`.

The sealed responses corroborate this: the HTTP `Server` header is `BaseHTTP/0.6 Python/3.12.5`, not an independently launched CAPPO Uvicorn/FastAPI service.

This harness does **not** mechanically exercise:

- `cappo_backend.api.main:app`;
- `CapabilityHandler.execute()`;
- the real Biscuit verifier;
- the canonical replay backend;
- WAL persistence/recovery;
- the VRE substrate;
- the independent target observer.

## Evidence timestamp defect

The script hard-codes `2026-09-09T13:51:00Z` into every evidence record and into `contract_gate_result.json`. The raw HTTP responses carry `Date: Wed, 09 Sep 2026 20:51:19 GMT`. The evidence timestamp is therefore not a measured runtime timestamp and must not be described as a verbatim sealed execution time.

## Contract drift visible in the simulated responses

1. Test E/F return HTTP 422. The normative contract must explicitly declare 422 or the runtime mapping must change.
2. Exact replay returns HTTP 423. Freeze replay semantics explicitly; do not let it inherit the locked-state code by accident.
3. Reconciliation unavailability is returned as HTTP 423 from `/v1/consequence/reconcile`, while canonical doctrine reserves 503 for observer/target infrastructure unavailability and 423 for redispatch against an already locked execution.
4. Recovery status is misspelled `RECONCILIED_SUCCEEDED`; canonical spelling is `RECONCILED_SUCCEEDED`.
5. Valid dispatch returns `AUTHORIZED_FOR_MATERIALIZATION`; verify against the normative `DispatchResponse` schema before treating it as contract-conformant.

## Antigravity delta — do not redo lower proofs

Antigravity should consume the Notebook harness as a useful request/response fixture set, then convert it into a **client-only** hostile battery that never starts its own server.

Required real-runtime proof sequence:

1. Start the canonical CAPPO service independently, e.g. `uvicorn cappo_backend.api.main:app --host 127.0.0.1 --port 8002` with the required DB/Redis/test fixtures.
2. Assert readiness from a real CAPPO endpoint before testing.
3. Run a client-only hostile battery against that existing process.
4. Use real server-minted authority/leases; do not accept arbitrary `Bearer valid_server_biscuit_token` strings.
5. Record actual timestamps at request/response time.
6. Seal raw responses without fabricating CAPPO-specific headers in the test harness.
7. Validate every raw response against the exact OpenAPI file on the same commit.
8. Run CI and capture workflow/job IDs plus commit SHA.
9. Only then set `live_runtime_proof_passed=true` and create/tag an RC.

## Truth status

- PRODUCT-0 A–L defined proof battery: **CLOSED at recorded proof surfaces**.
- Notebook static contract gate: **VALID at its local/static proof surface**, subject to the known runner corrections already handed off.
- Notebook hostile port-8002 harness: **VALID SIMULATION / NOT canonical live-runtime proof**.
- Notebook `MERGE GATE SATISFIED` banner: **INVALID for canonical merge evidence**.
- Notebook `v0.9.0-PRODUCT-0-RC1` GitHub tag: **NOT FOUND at review time**.
- Canonical CAPPO live port-8002 proof: **PENDING** until an independently started real service is exercised and raw evidence is landed on GitHub.
