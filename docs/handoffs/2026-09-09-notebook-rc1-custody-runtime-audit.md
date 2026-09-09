# Notebook RC1 Custody / Runtime Audit — Canonical Branch Check

Date: 2026-09-09
Branch: `product0/notebook-contract-handoff-20260909`
PR: #99
Classification: NOTEBOOK-REPORTED / CANONICAL BRANCH AUDIT

## Verdict

The latest Notebook package is **not merge evidence** and must not be used to mark `v0.9.0-PRODUCT-0-RC1` merge-ready.

The canonical branch still proves the route/issuer seam and the previously accepted lower-layer PRODUCT-0 proofs, but the new Notebook claims for server-held Biscuit custody, 11/11 live runtime, 6/6 static gate, SDK sync, RC tag, competitor uniqueness, and 7/7 friction are not repository-canonical.

Current release status remains **DRAFT / NOT MERGE-ELIGIBLE**.

## 1. Canonical route and issuer seam — VALID

On the branch, `cappo_backend/api/routers/capability_mount_router.py` defines the `/v1/capability` router prefix and `POST /mounts`, so the canonical mount route is:

`POST /v1/capability/mounts`

The route authenticates principal/workspace context and calls `MountRegistry.request_mount(...)`. `MountRegistry` performs server-side `mint_biscuit_capability(...)` issuance.

This part of Notebook's latest report is directionally correct.

## 2. Server-held Biscuit response custody — NOT YET LANDED

The current branch response model is still:

`MountResponse.token: EphemeralScopedToken | None`

and successful mount returns:

`token=record.token`

`EphemeralScopedToken` still contains:

`biscuit_token: str | None = None`

Therefore the branch route itself still serializes raw Biscuit material unless a separate unobserved middleware removes it. Existing branch tests explicitly read `mount["token"]["biscuit_token"]`, confirming the current test contract expects the raw token in the mount response.

Notebook's uploaded OpenAPI saying `SERVER_HELD_BISCUIT_CUSTODY` does not change the FastAPI serializer.

**Status:** NEEDS WIRING.

### Minimal implementation rule

Preserve the internal persisted Biscuit and server-side minting. Remove `biscuit_token` only from the external mount response contract. Do not invent a new authority architecture. Update tests that currently consume the raw response token to obtain internal Biscuit evidence from the persistence/service boundary when cryptographic verification is needed.

## 3. Notebook's uploaded 11/11 hostile battery — INVALID AS LIVE CAPPO PROOF

The uploaded `run_hostile_port8002_battery.py` imports `HTTPServer` and `BaseHTTPRequestHandler`, defines `CAPPOHostileServerHandler`, starts its own server on port 8002, and then tests that same in-process simulated server.

It also hard-codes response behavior, including:

- synthetic mount IDs / lease IDs;
- arbitrary strings such as `Bearer valid_server_biscuit_token`;
- hard-coded test timestamp `2026-09-09T14:50:00Z`;
- synthetic `server_held_biscuit_custody: true`;
- `AUTHORIZED_FOR_MATERIALIZATION` response state;
- misspelled `RECONCILIED_SUCCEEDED`;
- target-unavailable reconciliation as 423 instead of the frozen 503 infrastructure-unavailable semantics.

Therefore its `11/11` result is a **VALID SIMULATION / INVALID canonical live-runtime proof**.

The canonical branch already contains the corrected client-only runner that explicitly refuses a `BaseHTTP/` simulator and requires an independently running CAPPO runtime plus deterministic fixtures. That branch runner remains authoritative.

## 4. Notebook static gate — NOT CANONICAL

The uploaded `verify_product0_contract.py`:

- inserts hard-coded `/workspace/scratch` into `sys.path`;
- imports four test classes from that scratch layout;
- prints a fixed rule table after the unittest result;
- does not enforce the previously required exact named-test count / zero-skips semantics;
- is not present at `scripts/verify_product0_contract.py` on this branch.

The uploaded `contract_gate_result.json` is a self-declared summary, not an independently derived GitHub CI result. Its `PASSED_VERIFIED_FOR_MERGE` state cannot close PR #99.

## 5. Uploaded OpenAPI — useful draft, still inconsistent

Useful correction:

- mount route is now `/v1/capability/mounts`.

Remaining problems:

- `CapabilityMountResponse` is a flat `{mount_id, lease_id, execution_id, envelope_digest}` object, while the current branch runtime returns the nested `MountResponse` model containing `decision`, `reason`, `anchoring`, `mount`, `token`, TTL/expiry state;
- the uploaded contract declares server-held Biscuit custody before the runtime serializer implements it;
- reconciliation text simultaneously declares 503 as infrastructure-unavailable and also describes 423 as target unreachable, which blurs the frozen semantic boundary;
- it is not present on the branch at `contracts/cappo-consequence-dispatch.openapi.yaml`.

Do not call the SDKs OpenAPI-generated or contract-conformant until runtime and contract share one response schema on the same commit.

## 6. SDK sync claim — FALSE ON CANONICAL BRANCH

Current branch SDKs are still provisional and both call `/v1/mounts`, not `/v1/capability/mounts`.

They also currently model the mount result as the flat descriptor shape rather than the actual nested branch response.

Notebook may have updated Studio copies, but those copies have not landed in GitHub and are not canonical.

## 7. RC tag / merge status — NOT VERIFIED

GitHub currently returns no `v0.9.0-PRODUCT-0-RC1` tag.

PR #99 remains open and **draft**. It is mergeable in the Git sense, but its own merge criteria are not satisfied and it is explicitly marked `DRAFT — NOT MERGE-ELIGIBLE YET`.

Do not create the tag or final merge announcement until the same commit has real CI/runtime evidence.

## 8. Wedge / competitor / friction claims — remain blocked

The uploaded dossier and executive PDF still assert `UNIQUE WEDGE SUPPORTED`, 5/5 competitor reproduction, and 7/7 friction. Those conclusions remain invalid because:

- Notebook competitor tests were simulations, not provider reproductions;
- the 0.9883 s friction value was previously proven to be time-to-connection-failure in the false-green benchmark;
- the fail-closed branch friction runner has not yet produced a real seven-metric PASS;
- canonical W1-A through W1-D integrated runtime proof is still incomplete.

The dossier verdict remains `INDETERMINATE`.

## 9. PRODUCT-0 hard-deadline correction

Do not reintroduce the old `deadline_stall` thread-abandonment caveat as the current Test H state. The defined Test H was already closed by the separate process-supervisor proof using process-group `SIGKILL` and reaping (`rc=-9`). The remaining work is integration of that proven invariant into the intended live pipeline, not a new Test H.

## Exact next delta for Antigravity

1. Patch the actual branch FastAPI mount response so `biscuit_token` is excluded externally while remaining available internally/persisted.
2. Update affected branch tests to assert no external raw Biscuit and verify internal Biscuit at the persistence/service boundary.
3. Align the OpenAPI mount request/response schema to the actual runtime model chosen on that same commit.
4. Align both SDKs to the canonical mount route and the same response model; keep them provisional until schema generation is reproducible.
5. Provision deterministic server-side fixtures; the hostile client must not mint its own trusted authority.
6. Start canonical Uvicorn independently and run the **branch client-only** hostile battery.
7. Preserve 503 for reconciliation infrastructure unavailable; use 423 for subsequent retry/dispatch while locked.
8. Land repository-relative static verifier + workflow and seal real run metadata (commit SHA, CI run/job IDs, measured timestamps).
9. Only then reassess PR readiness and RC tag creation.

## Frozen truth

- PRODUCT-0 A-L: **CLOSED at recorded proof surfaces**.
- Canonical mount route: **VALID — `/v1/capability/mounts`**.
- Server-side Biscuit minting seam: **VALID**.
- External server-held Biscuit custody on current branch: **NOT YET IMPLEMENTED / NEEDS WIRING**.
- Notebook 11/11 hostile result: **VALID SIMULATION / INVALID live proof**.
- Notebook 6/6 static gate: **NOT repository-canonical**.
- SDK sync: **NOT LANDED**.
- RC tag: **ABSENT**.
- PR #99: **DRAFT / NOT MERGE-ELIGIBLE**.
- Final Wedge verdict: **INDETERMINATE**.
