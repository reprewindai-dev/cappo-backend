# CAPPO Migration Mapping Note

**From:** `veklom-byos-backend` (cloned as `reprewindai-dev/veklom-byos-backend`)
**To:** new CAPPO backend (greenfield, not yet created)
**Type:** Documentation-only migration planning note. No code changes. No canonical-doc edits.
**Date:** 2026-06-08

---

## 0. Scope, truth order, and how this note was produced

This note maps the seven old-backend areas requested (PGL model, PGL client, orchestrator, MCP gateway, exec router, audit logging, middleware / payment gate) into the planned CAPPO backend structure. For each area it answers the five required questions:

1. **What exists today**
2. **What is still valid conceptually**
3. **What must not be carried forward**
4. **What maps into the new CAPPO backend structure**
5. **What can be retired after migration**

Truth order followed: human instructions > canonical CAPPO docs > approved EI implementation-planning draft > old `veklom-byos-backend` as **lineage/reference only**.

**Verification method:** the old backend was read directly. Symbol/file existence was confirmed with repository-wide search (excluding `.git`/`node_modules`). Every "does not exist" below is an empirical result, not an assumption.

### Critical contradiction surfaced during verification (required reading before Phase 1)

The Lineage & Gap Brief and the EI Implementation Plan list three "confirmed lineage anchors" at exact file:line locations. **None of these files or symbols exist in the old backend** (verified in `veklom-byos-backend`, cross-checked in `lockerphycer` + 6 other UACP repos):

| Doc claim | Reality in old backend |
|---|---|
| `PGLCertificate` in `backend/db/models/pgl.py:26-42` | No file `pgl.py`; **zero** matches for `PGLCertificate` |
| `VeklomRunStateMachine` in `backend/services/orchestrator.py:7-24`, `commit_run()@104` | No `backend/services/` dir; **zero** matches for `VeklomRunStateMachine` / `commit_run` / `govern_run` / `route_run` |
| `MCPGateway` in `backend/core/security/mcp_gateway.py:33-66` | No file `mcp_gateway.py`; **zero** matches for `MCPGateway` |
| `PGLClient` in `backend/services/pgl_client.py` | Does not exist; **zero** matches for `PGLClient` / `pgl` (non-git) |
| `SEKED` attestation, `seked_state`, `v4_decision`, `pgl_identity` | **zero** matches |

This is consistent with the Lineage Brief's own statement that *"The CAPPO repository does not exist yet"* — the PGL/orchestrator/SEKED/MCP-gateway governance kernel is **doctrine/specification**, not shipped code. The old backend instead contains **adjacent, post-hoc** equivalents (a write-through run audit record, a hash-chained AI audit log, a zero-trust auth middleware, a budget/kill-switch payment gate). The migration is therefore **forward-construction onto lineage seeds**, not a port of existing governance objects.

**Implication for Phase 1:** "re-read the old PGL model / orchestrator / MCP gateway" cannot be performed literally because they don't exist. The valid lineage seeds to carry forward are named per-area below.

---

## 1. PGL model

**Old location(s):** none. Closest lineage seed: `backend/db/models/veklom_run.py` (`VeklomRun`); provenance hashing helper `_sha256_json()` and `AIAuditLog` hash chain.

### 1. What exists today
- No `PGLCertificate` model and no `pgl.py`.
- `VeklomRun` (`backend/db/models/veklom_run.py:12-68`) is a **durable write-through audit/proof record** ("the durable V5 bridge between legacy request logs and the deterministic run object"). It already carries provenance-style fields: `genome_hash`, `input_hash`, `output_hash`, `decision_frame_hash`, `constitution_hash` (default `"unsealed"`), `provenance_json`, `governance_decision` (default `"ALLOW"`), `risk_tier`, plus budget fields (`approved_budget_cents`, `reserve_before/after_cents`, `over_budget`) and `status` (default `"SEALED"`).
- It is created **after** execution from a request log (`_record_veklom_run_from_request_log`, `core/services/workspace_gateway.py:168`), with `governance_decision`/`risk_tier` **derived from final status**, not computed by a governor.
- JSON is stored as SQLAlchemy `Text` columns (string-encoded), not native `JSONB`. (`AIAuditLog` does use the `JSON` column type — so JSON columns are available in the stack.)

### 2. What is still valid conceptually
- The **field vocabulary** is directly reusable as PGL seed inputs: `genome_hash`, `constitution_hash`, `plan_hash` (to add), `input_hash`, `output_hash`, `decision_frame_hash`, `provenance_json`.
- The notion of a **single canonical run record** keyed by `run_id` + `workspace_id` + `tenant_id` + `delegation_depth` is sound and aligns with the CAPPO governed-run entity.
- `_sha256_json()` canonical hashing helper is a valid seed for canonical serialization/hashing.

### 3. What must not be carried forward
- **Post-hoc, status-derived governance.** `governance_decision = "ALLOW" if status=="SEALED" else "HOLD"` and `risk_tier` defaults must NOT survive — CAPPO requires governance/PGL decided **before** execution, not inferred after sealing.
- **`status` default `"SEALED"`** and the implicit "record after the fact" lifecycle.
- **`constitution_hash` default `"unsealed"`** as an acceptable steady state.
- Storing structured proof payloads as opaque `Text` blobs where queryable `JSON`/`JSONB` is appropriate.

### 4. What maps into the new CAPPO backend structure
- New `PGLCertificate` model (CAPPO data layer) seeded from `VeklomRun`'s hash field set, plus pre/post linkage (`pre_execution_certificate_id` / `post_execution_certificate_id`), `outcome_hash`, and `persisted` provenance.
- New `PGLLedgerEvent` model for hash-chained certificate lifecycle events (pattern borrowed from `AIAuditLog.previous_log_hash` chaining — see §6).
- `VeklomRun` → CAPPO **governed run entity** (pre-execution lifecycle, see §3), retaining the proven hash columns as first-class typed JSON.

### 5. What can be retired after migration
- The status-derived governance assignment logic in `_record_veklom_run_from_request_log`.
- `VeklomRun` as a *purely post-hoc* audit bridge, once the governed-run entity owns the pre-execution lifecycle (the write-through indexing can remain as a downstream projection only).

---

## 2. PGL client

**Old location(s):** none. (`PGLClient` does not exist; provider calls live in `core/llm/*`.)

### 1. What exists today
- No `PGLClient` and no PGL simulation/fallback code.
- Provider execution is handled by `core/llm/ollama_client.py`, `core/llm/groq_fallback.py`, and `core/llm/circuit_breaker.py` — none of which produce certificates.
- There is therefore **no existing "simulated PGL fallback returning `persisted: False`"** to disable; that risk is specified in doctrine for the new client.

### 2. What is still valid conceptually
- The **circuit-breaker + fallback discipline** (`record_failure` / `record_success` / `is_open`) is a sound resilience pattern and a good model for how a real `PGLClient` should treat DB availability (fail-closed rather than silently degrade).
- Clean separation of provider clients behind a small interface is worth preserving.

### 3. What must not be carried forward
- Any **silent degradation** semantics. The new `PGLClient` must implement the doctrine guard: in production (`CAPPO_REQUIRE_PERSISTENT_PGL=true`) a missing DB session must raise, never fall back to a non-persisted certificate.
- No legacy "simulation shortcut" should be introduced and then guarded — it should not exist as a usable path in production at all.

### 4. What maps into the new CAPPO backend structure
- New `PGLClient` in the CAPPO **PGL service** boundary: mints/persists `PGLCertificate` rows and `PGLLedgerEvent`s, with an explicit `persisted: bool` contract.
- Fail-closed constructor guard wired to config (see §7) so production cannot accept simulated/non-persisted certificates.

### 5. What can be retired after migration
- Nothing to retire (no prior implementation). The LLM provider clients/circuit breaker remain in the execution layer and are referenced by, not replaced by, the PGL client.

---

## 3. Orchestrator

**Old location(s):** none. No `backend/services/orchestrator.py`, no `VeklomRunStateMachine`, no `RunOrchestrator`. Closest lineage seeds: `core/services/workspace_gateway.py` (run write-through) and `apps/api/workflows.py` (Upstash maintenance).

### 1. What exists today
- **No orchestrator and no run state machine.** Zero matches for `govern_run`/`commit_run`/`route_run`/`contextualize`/`attest`.
- The only "run" lifecycle is the **post-hoc projection** in `core/services/workspace_gateway.py` (`_record_veklom_run_from_request_log`, `_publish_veklom_run_signal`, `index_veklom_run`) plus the Upstash maintenance workflow in `apps/api/workflows.py` that backfills/indexes existing `VeklomRun` proofs. Neither governs, commits, mints, or routes a run before execution.

### 2. What is still valid conceptually
- The **vocabulary of phases** (GOVERNED → COMMITTED → ROUTED → EXECUTING) and the run-signal/proof-indexing idea are reusable.
- The Redis/Upstash **signal publication** of run state (`_publish_veklom_run_signal`) is a valid downstream hook for an orchestrator's emitted events.

### 3. What must not be carried forward
- The **"derive a run record after the request already executed"** pattern. CAPPO's orchestrator must own the run **before** any side effect.
- Implicit ALLOW defaults and absence of an explicit mint point.

### 4. What maps into the new CAPPO backend structure
- New CAPPO **orchestrator service** implementing the explicit method sequence: `create_run → compile_run → contextualize_run → govern_run → commit_run → mint_execution_identity → route_run → execute_run → attest_run`.
- The EI mint must occur **after `commit_run()` and before `route_run()`** (per the EI Implementation Plan). `commit_run()` is the PGL pre-certificate mint point.
- `workspace_gateway` proof write-through / `workflows.py` indexing become **downstream projections/attestation sinks** fed by the orchestrator, not the source of run truth.

### 5. What can be retired after migration
- Post-hoc `VeklomRun` derivation as the run's authoritative lifecycle.
- Status-to-governance inference once `govern_run()` produces real decisions.

---

## 4. MCP gateway

**Old location(s):** none. No `mcp_gateway.py`, no `MCPGateway`. Closest lineage seed: `core/security/zero_trust.py` (`ZeroTrustMiddleware`) and `apps/api/middleware/request_security.py` / `locker_security_integration.py`.

### 1. What exists today
- **No `MCPGateway` and no proof-derived authority checks anywhere.** The brief's described MCPGateway checks (injection scanning, tool-registry hash, egress allowlist, file-access blocking) are **not present** as an `MCPGateway`.
- `ZeroTrustMiddleware` (`core/security/zero_trust.py:89`) enforces **auth only**: JWT Bearer / API-key validation, public-path allowlist, and path-traversal blocking. Notably it lists **`/v1/exec` as a public path** (`zero_trust.py:29`) because that route does its own `X-API-Key` + tenant RLS auth.
- `RequestSecurityMiddleware` and `LockerSecurityMiddleware` provide request hardening/IDS-style checks, but none validate execution identity, PGL, scope, budget-as-authority, delegation depth, or revocation.

### 2. What is still valid conceptually
- The **single choke-point middleware** pattern (one place every request passes through) is exactly the right shape for the CAPPO enforcement boundary.
- Public-path allowlisting and path-traversal blocking are reusable hygiene.

### 3. What must not be carried forward
- **`/v1/exec` as a public/bypass path.** This is the LAW 0 bypass — it must NOT be reproduced in CAPPO.
- The assumption that **auth == authority**. CAPPO enforcement must require a valid `ExecutionIdentityV1` (proof-derived authority), not merely a valid key/token.

### 4. What maps into the new CAPPO backend structure
- New CAPPO **MCP gateway / enforcement boundary** exposing `require_execution_identity(...)` that validates: real persisted PGL reference, hash alignment, directive permits execution, TTL/`expires_at`, scope covers action, budget covers cost, delegation depth ≤ max, signature+hash verify, not revoked.
- Rejection contract: HTTP 403 `{"error":"EXECUTION_IDENTITY_REQUIRED","detail":"…","law0":true}`, logged to the audit log with `operation_type="law0_violation"`; kill-switch 402 takes precedence (see §7).
- Auth hygiene from `ZeroTrustMiddleware` (allowlist, traversal blocking) carries over as a **separate, earlier** layer — authentication first, then authority enforcement.

### 5. What can be retired after migration
- Treating `/v1/exec` (or any side-effecting route) as an unguarded public path.
- Any reliance on middleware that stops at authentication for side-effecting tool calls.

---

## 5. Exec router

**Old location:** `backend/apps/api/routers/exec_router.py` (402 lines). *(Note: actual path is `apps/api/routers/`, not the `api/routers/` cited in some docs.)*

### 1. What exists today
- `POST /v1/exec` executes LLM calls **directly**: `X-API-Key` → tenant/workspace resolve (`_resolve_api_key`), Postgres RLS (`_set_rls`), optional Redis conversation memory, then Ollama with a **self-healing circuit breaker** that auto-routes to Groq on failure, and writes an `ExecutionLog` (`_log_execution`).
- **No governance, PGL, execution identity, SEKED, or financial-budget authority check** in the handler (only an LLM **latency** budget, `exec_router.py:171`). The only financial/kill gating is the upstream `BudgetCheckMiddleware` (402) and kill switch — i.e. it "checks only kill-switch/budget state, not proof-derived authority."
- `GET /status` returns system health (db/redis/llm/circuit breaker).

### 2. What is still valid conceptually
- The **response shape** (`ExecResponse`: response/model/provider/tokens/latency/log_id/conversation_id) is a good external contract to preserve for the CAPPO governed entry path.
- **Tenant isolation** via API-key resolution + RLS, **conversation memory**, and **circuit-breaker resilience** are valuable execution-layer mechanics.

### 3. What must not be carried forward
- **The bypass itself.** Direct provider execution with no enforcement object, and `/v1/exec` being on the zero-trust public allowlist, must not be recreated.
- Auth/governance living **inside** the route handler instead of at the enforcement choke point.

### 4. What maps into the new CAPPO backend structure
- A **single governed execution entry path** (Option A of the EI plan): create→govern→commit→mint EI→route→execute→attest through the orchestrator, with the MCP gateway enforcing EI before any side effect. Preserve the current response shape.
- Tenant resolution + RLS + circuit breaker + conversation memory become **execution-layer components invoked by the governed path**, not a standalone ungoverned route.

### 5. What can be retired after migration
- `/v1/exec` as an independent, ungoverned route, once the governed entry path is the only side-effecting execution path.
- `ExecutionLog`-only recording, superseded by governed-run + PGL + EI + audit records (ExecutionLog may remain as a thin operational log).

---

## 6. Audit logging

**Old location:** `backend/db/models/ai_audit.py` (`AIAuditLog`, hash-chained), referenced across ~30 files; plus `db/models/security_audit.py`, `security_event.py`, `execution_log.py`.

### 1. What exists today
- `AIAuditLog` (`ai_audit.py:9`) is an **immutable, hash-chained** record: `input_hash`/`output_hash`, `log_hash` (HMAC-SHA256 of the entry) and `previous_log_hash` (chaining), `operation_type`, provider/model, cost/tokens, PII flags, routing linkage. Uses the `JSON` column type.
- This is the strongest existing lineage seed for a **ledger** and is already the destination the EI rejection contract expects (`operation_type="law0_violation"`).

### 2. What is still valid conceptually
- **Hash chaining** (`previous_log_hash` → tamper-evident ledger) is exactly the pattern CAPPO needs for `PGLLedgerEvent` and LAW 0 audit events.
- `operation_type` as a typed discriminator (extensible to `law0_violation`) is reusable as-is.
- Hash-of-payload integrity + PII flagging are sound.

### 3. What must not be carried forward
- Audit writes that are **best-effort / swallowed** (the exec path's `_log_execution` rolls back and returns `None` on failure). LAW 0 audit events must be reliable/fail-loud, not silently dropped.
- Audit logging scattered ad-hoc across ~30 call sites instead of behind one audit/ledger service.

### 4. What maps into the new CAPPO backend structure
- New CAPPO **audit/ledger service boundary** with an **audit event model** seeded from `AIAuditLog` (hash chain + `operation_type`) and a **`PGLLedgerEvent`** model reusing the chaining pattern.
- LAW 0 violations and EI lifecycle events route through this single service; `operation_type="law0_violation"` is a first-class event type.

### 5. What can be retired after migration
- Direct, scattered `AIAuditLog` writes from routers/middleware once the audit/ledger service owns emission.
- Best-effort swallow-on-error semantics for governance-critical events.

---

## 7. Middleware / payment gate

**Old location:** `apps/api/main.py` middleware stack (lines 147-176); `apps/api/middleware/budget_check.py` (402), `entitlement_check.py`, `token_deduction.py`, `rate_limit.py`, `request_security.py`; `core/cost_intelligence/kill_switch.py`; `apps/api/routers/kill_switch.py`; `license/middleware.py`.

### 1. What exists today
- A deep middleware stack (outer→inner): `LockerSecurity → RequestSecurity → RateLimit → ZeroTrust → LicenseGate → EntitlementCheck → Metrics → IntelligentRouting → EdgeRouting → BudgetCheck → CORS → Gzip → Performance → FastPath`.
- **Payment gate:** `BudgetCheckMiddleware` returns **HTTP 402** on budget exhaustion or active kill switch (`budget_check.py:77,104,114`); manual kill switch via `routers/kill_switch.py` (Redis `kill_switch:{workspace_id}`, "402 on all future AI requests until deactivated"). `EntitlementCheckMiddleware`/`LicenseGateMiddleware` gate by plan/license.
- **Authority gap:** none of these validate proof-derived authority (EI/PGL/SEKED). Gating is financial/license/auth only.

### 2. What is still valid conceptually
- The **402 kill-switch/budget precedence** is explicitly required by CAPPO doctrine: kill-switch 402 must take precedence over the LAW 0 403. The existing kill-switch/budget mechanism is the correct lineage for that precedence rule.
- Ordered middleware layering (security → auth → entitlement → budget) is a good structure; CAPPO inserts EI enforcement after auth/entitlement and before execution.
- Redis-backed kill switch with auto-restore is reusable operationally.

### 3. What must not be carried forward
- The idea that **passing budget/kill/license/auth == permission to execute**. CAPPO requires EI authority on top of these.
- Recreating exemptions (e.g. `/v1/exec` public-path bypass) within the new stack.

### 4. What maps into the new CAPPO backend structure
- Kill-switch/budget 402 logic → CAPPO **payment/cost gate**, positioned so its 402 **precedes** the MCP gateway's LAW 0 403.
- License/entitlement gates → CAPPO entitlement layer (auth/entitlement remain *before* authority enforcement).
- New **EI enforcement** layer (the MCP gateway, §4) added as the authority checkpoint before any side-effecting execution.

### 5. What can be retired after migration
- Any per-route public-path bypasses for side-effecting endpoints.
- Middleware-level assumptions that financial/license gating is the final permission check.

---

## 8. Config / production discipline (cross-cutting, supports §2 and §7)

- **Exists today:** `core/config.py` `Settings` (pydantic-settings, `.env`). Has `secret_key` (default `"change-me-in-production-use-env-var"`), `qstash_current/next_signing_key`, `stripe_secret_key`, `encryption_key`, `sentry_environment`. **No** dedicated EI signing key, **no** `CAPPO_REQUIRE_PERSISTENT_PGL`, **no** explicit `is_production`/`environment` fail-closed flag.
- **Maps to CAPPO:** add explicit config boundaries — EI signing key requirement, `CAPPO_REQUIRE_PERSISTENT_PGL=true` (fail-closed PGL), and an environment flag driving fail-closed behavior. `secret_key`/`encryption_key` patterns are the lineage for required-secret handling but must not default to placeholders in production.
- **Migrations:** old backend uses **Alembic** (`db/migrations/env.py`, `script.py.mako`, 16 versions). CAPPO keeps the Alembic scaffold pattern for governed-run / PGL / ledger / EI / audit tables.

---

## 9. Summary table

| Old area | Exists? | Lineage seed to keep | Must NOT carry forward | New CAPPO home |
|---|---|---|---|---|
| PGL model | No | `VeklomRun` hash fields; `_sha256_json` | post-hoc/status-derived governance; Text-blob JSON | `PGLCertificate` + `PGLLedgerEvent` (data layer) |
| PGL client | No | circuit-breaker/fail-closed discipline | silent degradation / sim fallback | `PGLClient` (PGL service) + fail-closed guard |
| Orchestrator | No | phase vocabulary; run-signal publish | post-hoc run derivation; ALLOW defaults | orchestrator service (explicit phase sequence + EI mint) |
| MCP gateway | No | single choke-point; allowlist/traversal | `/v1/exec` public bypass; auth==authority | enforcement boundary w/ `require_execution_identity()` |
| Exec router | Yes (`exec_router.py`) | response shape; RLS; memory; breaker | the ungoverned bypass | one governed execution entry path |
| Audit logging | Yes (`AIAuditLog`) | hash chain; `operation_type` | best-effort swallow; scattered writes | audit/ledger service + `law0_violation` events |
| Middleware / payment gate | Yes (402 kill/budget) | 402 precedence; ordered layering | financial/auth == permission | cost gate (402 precedes LAW 0 403) + EI enforcement layer |

---

## 10. Net migration posture

- The old backend supplies **lineage seeds and proven mechanics** (run hash vocabulary, hash-chained audit ledger, tenant RLS, circuit breaker, kill-switch 402 precedence, Alembic migrations, single-choke-point middleware) — but **not** the governance kernel itself.
- The CAPPO Phase 1 work is **forward construction** of `PGLCertificate`/`PGLLedgerEvent`/orchestrator/`ExecutionIdentityV1`/MCP-gateway/fail-closed config, seeded by those mechanics — **not** a port of existing PGL/orchestrator/MCP-gateway code (which does not exist).
- The single most important "must-not-carry-forward" across areas: **`/v1/exec` as an ungoverned, public-allowlisted, direct-execution bypass**, and the broader pattern of treating auth/budget/license as sufficient permission to execute.
