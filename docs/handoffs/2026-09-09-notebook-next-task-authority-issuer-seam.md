# Notebook Next Task — Verify CAPPO Trusted Authority Issuer Seam

Date: 2026-09-09
Branch: `product0/notebook-contract-handoff-20260909`
PR: #99
Priority: P0 — this task unblocks the canonical live runtime battery and real TTFGC benchmark.

## Why this is next

Do **not** run more AWS/Cloudflare simulations. Competitor simulations have already been rejected as evidence.

Do **not** benchmark TTFGC yet. The live benchmark requires a legitimate server-side authority issuance path and deterministic runtime fixtures first.

Do **not** spend the primary track re-documenting VRE-0. The H/I/J physical substrate proofs are already accepted at their recorded proof surfaces.

The next unresolved dependency is the **trusted authority issuer seam** used by a real client before `/v1/consequence/dispatch`.

## Current canonical clues to verify

On the current canonical main snapshot:

1. `cappo_backend/api/routers/capability_mount_router.py` mounts its router under prefix `/v1/capability` and exposes `POST /mounts`, making the actual route **`POST /v1/capability/mounts`** on that snapshot.
2. That route requires authenticated `auth_principal` and workspace state, then calls `MountRegistry.request_mount(...)` with execution scope plus caller/executor SPIFFE context.
3. `MountRegistry` uses `mint_biscuit_capability` internally, so the mount lifecycle is a plausible trusted server-side issuance seam.
4. `EphemeralScopedToken` currently has an optional `biscuit_token` field, and `MountResponse` returns `record.token` on the successful mount response. This creates a **custody question**: determine whether raw Biscuit bytes are actually returned on the browser/session-facing path and whether that matches the intended server-side custody doctrine.
5. There is potential route drift between Notebook/OpenAPI drafts using `/v1/mounts` and current code using `/v1/capability/mounts`. Measure and resolve; do not silently alias it in documentation.

## Exact Notebook assignment

### CLAIM TO TEST

**Issuer Seam I1:** An authenticated caller can request a bounded capability mount through the canonical CAPPO server path; the server mints/binds authority to principal/workspace/execution/scope and returns only the authority material intentionally permitted by the custody profile. A hostile client cannot manufacture trusted machine authority by directly supplying a `Veklom-Authority` value or by locally minting a token outside the server issuer path.

### Required work

1. Trace the exact live code path from HTTP request to Biscuit minting:
   - router path and request model;
   - authentication middleware fields used (`auth_principal`, `auth_workspace`, SPIFFE fields);
   - `MountRegistry.request_mount(...)`;
   - call to `mint_biscuit_capability(...)`;
   - persisted mount/token fields;
   - exact HTTP response body.

2. Produce a field-level binding matrix showing where each of these is created and verified:
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

3. Resolve the custody question empirically from code/tests:
   - Does successful `POST /v1/capability/mounts` currently serialize `biscuit_token` to the caller?
   - If yes, classify which caller profile receives it and whether browser/session clients can see it.
   - If browser/session exposure exists, mark **NEEDS WIRING** and specify the minimal response-model change (opaque authority reference / token descriptor only) without inventing a new architecture.
   - If only trusted M2M callers receive raw Biscuit, identify the exact gating condition and test proving it.

4. Resolve contract/runtime route drift:
   - canonical runtime route observed in code;
   - route declared in the current OpenAPI draft;
   - exact correction required so SDK, hostile battery, and TTFGC use the same endpoint.

5. Define the deterministic **server-side test fixture** needed by the client-only hostile battery. The client test must not import `mint_biscuit_capability` to manufacture its own trusted authority.

6. Specify two hostile tests without simulating the server:
   - client-supplied `Veklom-Authority` / forged authority input is rejected or ignored;
   - authority minted for Execution A cannot be reused for Execution B.

## Evidence rules

Accepted:
- exact GitHub path + commit/ref;
- exact code excerpts;
- actual HTTP response if Notebook has a real runtime artifact;
- existing test output tied to named tests.

Not accepted:
- generated example JSON presented as runtime output;
- locally calling `mint_biscuit_capability` from the hostile client and calling that server issuance;
- inferred browser custody without inspecting the response model/serialization path;
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

Once I1 is resolved, the next order is:

1. establish the first real PRODUCT-0 governed user loop using the canonical issuer -> dispatch -> observer path;
2. run the real client-only hostile port-8002 battery;
3. measure FRIC-01 / TTFGC against that same path;
4. then perform real provider-specific competitor reproductions.
