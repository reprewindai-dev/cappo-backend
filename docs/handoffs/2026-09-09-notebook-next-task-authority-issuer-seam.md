# Notebook Next Task — Verify CAPPO Trusted Authority Issuer Seam

Date: 2026-09-09
Branch: `product0/notebook-contract-handoff-20260909`
PR: #99
Priority: P0 — this task unblocks the canonical live runtime battery and real TTFGC benchmark.

## Verified branch findings — do not guess alternate routes

The current branch has now been inspected directly.

1. `cappo_backend/api/routers/capability_mount_router.py` defines `APIRouter(prefix="/v1/capability")` and `@router.post("/mounts")`. The current branch route is therefore **`POST /v1/capability/mounts`**. Do not search for or invent `/v1/exec/mount` or `/v1/auth/mount` unless later code explicitly defines them.
2. The route requires `auth_principal`; when auth is enabled it also requires `auth_workspace` and enforces requested workspace equality.
3. The route passes `owner_principal`, `owner_workspace`, requested `execution_id`, and caller/executor SPIFFE context into `MountRegistry.request_mount(...)`.
4. `MountRegistry.request_mount(...)` calls `mint_biscuit_capability(...)` **inside the server service**, after rejecting missing/unverified caller or executor principals. It binds the mint call to caller SPIFFE, executor SPIFFE, capability/package id, grants, execution id, TTL, revocation scope, and epoch.
5. The service then copies the minted raw Biscuit into `EphemeralScopedToken.biscuit_token`, persists `token.model_dump(...)`, and stores a SHA-256 Biscuit hash on `CapabilityLease`.
6. `EphemeralScopedToken` includes `biscuit_token: str | None = None` in its response model.
7. On successful mount, `MountResponse` returns `token=record.token`. No response serializer exclusion for `biscuit_token` is present in this route.

### Immediate custody classification

**Server-side issuance path: VALID.**

**Browser/session raw-Biscuit custody claim: INVALID on the inspected branch unless an upstream transport layer strips the serialized field after FastAPI response-model serialization.** The route/model itself exposes the raw `biscuit_token` in the successful mount response.

Therefore the current minimal implementation delta is **NEEDS WIRING**: preserve server-side minting and persisted internal authority, but do not serialize raw Biscuit material to browser/session-facing mount responses. Use the existing token descriptor fields (`token_id`, `mount_id`, `execution_id`, package/scope/grants/policy/expiry/single-use/nonce state) or an existing opaque authority reference. Do not invent a new authority architecture unless necessary.

## Why this is next

Do **not** run more AWS/Cloudflare simulations. Competitor simulations have already been rejected as evidence.

Do **not** benchmark TTFGC yet. The live benchmark requires a legitimate server-side authority issuance/custody path and deterministic runtime fixtures first.

Do **not** spend the primary track re-documenting VRE-0. The H/I/J physical substrate proofs are already accepted at their recorded proof surfaces.

The next unresolved dependency is the **trusted authority issuer/custody seam** used by a real client before `/v1/consequence/dispatch`.

## Exact Notebook assignment

### CLAIM TO TEST

**Issuer Seam I1:** An authenticated caller can request a bounded capability mount through the canonical CAPPO server path; the server mints/binds authority to principal/workspace/execution/scope and returns only the authority material intentionally permitted by the custody profile. A hostile client cannot manufacture trusted machine authority by directly supplying a `Veklom-Authority` value or by locally minting a token outside the server issuer path.

### Required work

1. Confirm the verified branch call chain and identify any middleware/serializer that could alter the conclusion:
   - `POST /v1/capability/mounts`;
   - `_caller(...)` auth principal/workspace enforcement;
   - `MountRegistry.request_mount(...)`;
   - server-side `mint_biscuit_capability(...)`;
   - persisted mount/token/lease fields;
   - exact FastAPI response serialization.

2. Produce a field-level binding matrix for:
   - principal;
   - workspace;
   - capability/package;
   - execution_id;
   - caller SPIFFE ID;
   - executor SPIFFE ID;
   - TTL / expiry;
   - grants / resources / blocked actions;
   - token_id / nonce;
   - Biscuit token custody.

3. Resolve the custody defect, not the issuance architecture:
   - find whether any upstream middleware strips `biscuit_token` after the route returns;
   - if none exists, classify raw-browser/session exposure **VALID DEFECT / NEEDS WIRING**;
   - specify the minimum response-model change and exact test that proves raw Biscuit is absent while server-held execution still works;
   - if trusted M2M clients legitimately require raw Biscuit, identify an explicit profile/gating condition rather than exposing it by default.

4. Resolve contract/runtime route drift:
   - current runtime route: `/v1/capability/mounts`;
   - route declared in current OpenAPI draft;
   - exact correction/compatibility decision so SDK, hostile battery, and TTFGC use one canonical path.

5. Define deterministic **server-side fixtures** for the client-only hostile battery. The hostile client must never import `mint_biscuit_capability` to manufacture trusted authority.

6. Specify/run two real hostile tests:
   - client-supplied `Veklom-Authority` / forged authority input is rejected or ignored;
   - authority issued for Execution A cannot be reused for Execution B.

## Antigravity coordination

Antigravity's current local search was drifting toward possible `/v1/exec/mount` or `/v1/auth/mount` routes. Stop that search: the branch has already established `/v1/capability/mounts`.

Antigravity also attempted to recreate `scripts/benchmark_frictionless_metrics.py` locally through PowerShell/Python inline quoting and both attempts failed with parser/syntax errors. Do not infer the local benchmark file is valid from those failed writes. The canonical branch file remains the source of truth until copied byte-for-byte or otherwise verified locally.

## Evidence rules

Accepted:
- exact GitHub path + branch/commit;
- exact code excerpts;
- actual HTTP response from independently started CAPPO;
- existing test output tied to named tests.

Not accepted:
- generated example JSON presented as runtime output;
- locally calling `mint_biscuit_capability` from the hostile client and calling that server issuance;
- inferred browser custody without inspecting response serialization/middleware;
- changing PRODUCT-0 A-L numbering.

## Required output

Return one compact handoff with:

- `STATUS`: VALID / INVALID / INDETERMINATE for Issuer Seam I1;
- exact canonical route;
- exact issuer function/call chain;
- authority binding matrix;
- custody result;
- route/contract drift result;
- existing tests that support each claim;
- **ALREADY PROVEN** items Antigravity must not redo;
- **NEEDS WIRING** items only;
- exact files Antigravity should change;
- falsifier for each proposed change.

## What this unlocks

Once the custody defect and route alignment are resolved:

1. establish the first real PRODUCT-0 governed user loop using canonical issuer -> dispatch -> observer;
2. run the real client-only hostile port-8002 battery;
3. measure FRIC-01 / TTFGC against that same path;
4. then perform real provider-specific competitor reproductions.
