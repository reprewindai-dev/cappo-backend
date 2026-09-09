# Post-Merge Antigravity Delta — PRODUCT-0 / RC1

Date: 2026-09-09
Base: `main`
Merge commit: `e5b4fade259c83e22ba83d56a5356b9a7eb8f934`

## Purpose

PR #99 is merged. This handoff records only the work that still needs implementation/verification after that merge. Do not reopen PRODUCT-0 A-L; consume those proof artifacts as already closed at their recorded proof surfaces.

## Canonical facts on main after merge

1. Canonical mount route is `POST /v1/capability/mounts`.
2. Server-side mount issuance uses `MountRegistry.request_mount(...)` and internal Biscuit minting.
3. The current FastAPI `MountResponse` still contains `token: EphemeralScopedToken | None`, and successful mount returns `token=record.token`.
4. `EphemeralScopedToken` still carries `biscuit_token`; therefore server-held Biscuit response custody is **not yet enforced on main**.
5. Canonical Python and TypeScript SDKs on main are still provisional and still call `/v1/mounts`, not `/v1/capability/mounts`.
6. `contracts/cappo-consequence-dispatch.openapi.yaml` is not present on main.
7. `scripts/run_hostile_port8002_battery.py` on main is the safe client-only runner. It currently contains 7 cases and requires deterministic server-side fixture environment variables. It must remain client-only; do not replace it with Notebook's self-hosted `HTTPServer` simulator.
8. Notebook's uploaded `11/11` hostile runner, `6/6` gate, `MERGE GATE SATISFIED`, competitor `5/5`, and friction `7/7` outputs are not canonical runtime evidence.

## Required implementation delta

### A. Fix Biscuit response custody in the actual FastAPI route

Change the mount response boundary so the raw Biscuit never serializes to browser/session callers while preserving internal persisted authority.

Minimum acceptable implementation:
- introduce a public token descriptor model without `biscuit_token`, or explicitly serialize/exclude that field at the response boundary;
- keep raw Biscuit available internally for canonical execution verification;
- update all tests that currently inspect `mount["token"]["biscuit_token"]` from the HTTP response to inspect server-side persisted state when cryptographic authority validation is required;
- add a hostile response test asserting `biscuit_token` is absent from `POST /v1/capability/mounts` JSON.

Falsifier: any browser/session-facing mount response contains raw `biscuit_token`.

### B. Align SDKs to the real mount schema

Notebook uploaded newer SDK stubs that point to `/v1/capability/mounts`, but those files are Studio artifacts, not canonical main.

Do not copy them blindly because their flat request/response model (`capability_id`, `workspace_id`, `envelope_digest` -> flat `mount_id/lease_id/...`) does not currently match the actual FastAPI `MountRequest` / nested `MountResponse` model.

Instead:
- derive SDK request/response types from the final actual FastAPI/OpenAPI schema;
- use `POST /v1/capability/mounts`;
- expose only opaque/public descriptors;
- keep typed 401/403/422/423/503 error mapping;
- never manufacture or expose `Veklom-Authority`.

Falsifier: SDK integration cannot mount against the same-commit canonical FastAPI route or expects raw Biscuit material.

### C. Land a normative OpenAPI that matches runtime exactly

The uploaded Notebook OpenAPI has the correct route name but its flat `CapabilityMountResponse` does not match current runtime. Build/land the normative contract from the actual FastAPI request and public response models after custody is fixed.

Preserve semantics:
- 503 = target/observer reconciliation infrastructure unavailable;
- 423 = replay / redispatch while already locked;
- `RECONCILED_SUCCEEDED` spelling;
- no caller-supplied reconciliation URL;
- no raw Biscuit in browser/session response.

### D. Complete deterministic server-side runtime fixtures

The hostile client must consume trusted server-side fixtures or a real server issuance/setup path. It must never import `mint_biscuit_capability` to create trusted authority locally.

Fixtures needed by the current client-only runner:
- bearer/auth context;
- consumed lease;
- revoked lease;
- valid lease + execution;
- mismatch lease + execution;
- locked `OUTCOME_UNKNOWN` execution;
- reconciliation execution.

### E. Complete the live client-only battery

Keep current client-only architecture and add only real-runtime cases after endpoints/fixtures exist:
- envelope substitution;
- reconciliation target unavailable -> 503 + `RECONCILIATION_UNAVAILABLE`;
- reconciliation recovery -> 200 `RECONCILED_SUCCEEDED`;
- server-held Biscuit custody on `/v1/capability/mounts`.

Evidence requirements:
- independent Uvicorn/CAPPO process;
- real UTC timestamps;
- real response headers/body;
- exact commit SHA;
- non-zero exit on drift;
- no `BaseHTTPServer`/`HTTPServer` self-hosted simulation.

### F. Friction benchmark

Use the canonical fail-closed `scripts/benchmark_frictionless_metrics.py` already merged. Do not use Notebook's old false-green 0.9883-second result. Run only after the real mount/dispatch path and fixtures work.

### G. Wedge / competitor work

Do not spend implementation time on simulated competitor kill tests. The replacement verifier requires real provider reproduction evidence. Current dossier verdict remains `INDETERMINATE` until canonical W1 integration + real provider reproductions + real friction evidence are complete.

## Required Antigravity return

Return:
- changed file list;
- commit SHA / branch;
- raw test command output;
- exact FastAPI mount response JSON after custody patch;
- proof that persisted internal Biscuit still verifies while HTTP response contains none;
- client-only battery evidence against independent Uvicorn;
- any unresolved mismatch as `INDETERMINATE`, not PASS.
