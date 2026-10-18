# GATEWAY TRUST CONTRACT

> **Status**: Active — governs all inter-gateway communication  
> **Version**: 1.0.0  
> **Scope**: CAPPO Inside-MCP ↔ Edge-MCP trust boundary  
> **Last Reviewed**: 2026-06-11

---

## 1. Architecture Overview

The CAPPO runtime uses a **two-gateway split model**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                        SOVEREIGN RUNTIME                            │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              INSIDE MCP  (Authority Broker)                    │ │
│  │                                                                │ │
│  │  Sources of Truth:                                             │ │
│  │    • Birth/Identity Registry (Veklom BYOS)                     │ │
│  │    • UACP (Unified Agent Compliance Protocol)                  │ │
│  │    • Agent Ledger (immutable audit chain)                      │ │
│  │    • PGL Certificate Store                                     │ │
│  │                                                                │ │
│  │  Actions:                                                      │ │
│  │    • Consult sources of truth                                  │ │
│  │    • Mint ExecutionIdentityV1 (EI)                             │ │
│  │    • Mint Execution Authorization Token (EAT)                  │ │
│  │    • Record governance decisions to audit chain                │ │
│  │    • Revoke tokens and identities                              │ │
│  │                                                                │ │
│  │  NEVER:                                                        │ │
│  │    • Execute side effects                                      │ │
│  │    • Call external LLMs/tools                                  │ │
│  │    • Accept inbound requests from public internet              │ │
│  └────────────────────────────┬────────────────────────────────────┘ │
│                               │ EAT (signed, time-bound)            │
│                               ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │               EDGE MCP  (Execution/Ingress Surface)            │ │
│  │                                                                │ │
│  │  Actions:                                                      │ │
│  │    • Accept public HTTP/x402 requests                          │ │
│  │    • Verify EAT signature and claims                           │ │
│  │    • Execute approved side effects (LLM calls, tool calls)     │ │
│  │    • Accept x402 payments from external agents                 │ │
│  │    • Return results to callers                                 │ │
│  │                                                                │ │
│  │  NEVER:                                                        │ │
│  │    • Mint tokens or identities                                 │ │
│  │    • Bypass EAT verification                                   │ │
│  │    • Modify governance state                                   │ │
│  │    • Access sources of truth directly                          │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.1 Key Invariant

> **The Inside MCP is the ONLY entity that can mint execution tokens.  
> The Edge MCP can ONLY act when it holds a valid, unexpired, unrevoked EAT  
> signed by the Inside MCP.**

This is the fundamental security invariant. Every design decision flows from it.

### 1.2 The Gateway is Not the Source of Truth

The Inside MCP does not store governance state itself. It **consults** three external sources of truth:

| Source | What It Provides | How Gateway Uses It |
|--------|-------------------|---------------------|
| **Birth/Identity Registry** (Veklom BYOS) | Agent existence, status, genome hash, certificate, lineage | Agent validation before EAT minting |
| **UACP** (Unified Agent Compliance Protocol) | Compliance rules, jurisdiction constraints, safety requirements | Policy evaluation during governance |
| **Agent Ledger** (immutable audit chain) | Historical execution records, trust score events, violations | Trust score computation, revocation checks |

---

## 2. Execution Authorization Token (EAT)

The EAT is a signed, short-lived, single-use token that grants the Edge MCP permission to execute exactly one governed operation.

### 2.1 EAT Schema (v1)

```json
{
  "eat_version": "1.0",
  "eat_id": "eat-<uuid>",
  "execution_id": "<linked ExecutionIdentityV1.execution_id>",
  
  "subject": {
    "agent_id": "<veklom agent_id>",
    "certificate_id": "<PGL certificate_id>",
    "trust_score": 78.5,
    "risk_tier": "standard"
  },
  
  "authorization": {
    "directive": "ALLOW",
    "scope": {
      "tools": ["llm.exec"],
      "max_tokens": 4096,
      "allowed_models": ["gpt-4o", "claude-sonnet-4-20250514"]
    },
    "budget_approved_cents": 100,
    "budget_reserve_cents": 10,
    "single_use": true
  },
  
  "provenance": {
    "pgl_pre_certificate_id": "<uuid>",
    "genome_hash": "<sha256>",
    "constitution_hash": "<sha256>",
    "plan_hash": "<sha256>",
    "governance_decision_hash": "<sha256 of full governance frame>"
  },
  
  "temporal": {
    "issued_at": "2026-06-11T00:00:00Z",
    "expires_at": "2026-06-11T00:05:00Z",
    "ttl_seconds": 300
  },
  
  "issuer": "cappo-inside-mcp",
  "audience": "cappo-edge-mcp",
  "nonce": "<random 32-byte hex>",
  
  "signature": "<base64 ECDSA/HMAC signature over canonical body>",
  "hash": "<sha256 of canonical body>"
}
```

### 2.2 Field Semantics

| Field | Required | Description |
|-------|----------|-------------|
| `eat_version` | ✅ | Schema version. Edge MUST reject unknown versions. |
| `eat_id` | ✅ | Globally unique identifier for this token. Used for replay detection. |
| `execution_id` | ✅ | Links to the `ExecutionIdentityV1` minted during `mint_execution_identity()`. The EAT is an authorization envelope around an already-minted EI. |
| `subject.agent_id` | ✅ | The agent being authorized (validated against Veklom registry). |
| `subject.certificate_id` | ✅ | The PGL certificate backing this execution. |
| `subject.trust_score` | ✅ | Snapshot of trust score at mint time. Edge uses this for rate limiting. |
| `subject.risk_tier` | ✅ | `production`, `standard`, `sandbox`, or `terminated`. Edge enforces tier-specific constraints. |
| `authorization.directive` | ✅ | Must be `ALLOW` or `ALLOW_WITH_AUDIT`. Edge MUST reject all other values. |
| `authorization.scope` | ✅ | Tools and resource limits the Edge may execute. |
| `authorization.single_use` | ✅ | If true (default), the Edge MUST reject reuse. |
| `provenance.*` | ✅ | Hash chain linking this EAT back to the governance decision. The Edge does NOT re-validate these; it trusts the Inside MCP's signature over them. |
| `temporal.expires_at` | ✅ | Absolute expiration. Edge MUST reject expired tokens. |
| `nonce` | ✅ | Random value for replay protection. Edge MUST track seen nonces within the TTL window. |
| `signature` | ✅ | Cryptographic signature over the canonical body. |
| `hash` | ✅ | SHA-256 hash of the canonical body (integrity check before signature verification). |

### 2.3 Canonical Body (Signed Fields)

The signature and hash are computed over all fields **except** `signature` and `hash` themselves:

```python
def eat_canonical_body(eat: dict) -> dict:
    """Return the hashable/signable body of an EAT."""
    return {k: v for k, v in eat.items() if k not in ("signature", "hash")}
```

The canonical JSON serialization uses `json.dumps(body, sort_keys=True, separators=(",", ":"))` — no whitespace, sorted keys, deterministic output.

---

## 3. Trust Contract Rules

### 3.1 Inside MCP Obligations (Minting Side)

| # | Rule | Enforcement |
|---|------|-------------|
| M1 | **Agent Must Exist** | Validate `agent_id` against Veklom registry before minting. `AgentNotFoundError` → no EAT. |
| M2 | **Agent Must Be Active** | Agent status must be `active`. Revoked/suspended → no EAT. |
| M3 | **Trust Score Threshold** | Trust score > 40 (termination threshold). Score ≤ 40 → no EAT. |
| M4 | **Tool Authorization** | Requested tools must be in the agent's `tools` allowlist from its certificate. |
| M5 | **Safety Rules Compliance** | High-risk agents must have `human_escalation` in their safety rules. |
| M6 | **EI Must Be Minted First** | The `ExecutionIdentityV1` must be successfully minted and persisted before the EAT can be created. The EAT wraps an existing EI; it never exists without one. |
| M7 | **Single Signing Key** | All EATs for a given deployment are signed with the same key. Key rotation invalidates outstanding EATs (acceptable due to short TTL). |
| M8 | **TTL Must Be Short** | Default 300 seconds (5 minutes). Maximum 600 seconds (10 minutes). |
| M9 | **Audit Trail** | Every EAT mint is recorded in the audit chain with: `eat_id`, `execution_id`, `agent_id`, `issued_at`. |

### 3.2 Edge MCP Obligations (Verification Side)

| # | Rule | Enforcement |
|---|------|-------------|
| V1 | **Signature MUST Verify** | Edge verifies the EAT signature using the Inside MCP's public key. Invalid signature → HTTP 403. |
| V2 | **Hash MUST Match** | Edge recomputes the canonical body hash and compares. Mismatch → HTTP 403. |
| V3 | **Token MUST NOT Be Expired** | `expires_at` must be in the future. Expired → HTTP 403. |
| V4 | **Nonce MUST Be Unseen** | Edge maintains a nonce cache (TTL-bounded). Seen nonce → HTTP 403 (replay attack). |
| V5 | **Directive MUST Be ALLOW** | Only `ALLOW` or `ALLOW_WITH_AUDIT` are accepted. All other values → HTTP 403. |
| V6 | **Scope MUST Cover Action** | The requested tool/action must be in `authorization.scope.tools`. |
| V7 | **Budget MUST Cover Cost** | The action cost must not exceed `authorization.budget_approved_cents`. |
| V8 | **Single Use Enforced** | If `single_use` is true, the Edge MUST invalidate the EAT after first use. |
| V9 | **Version MUST Be Known** | Edge rejects unknown `eat_version` values (forward-compatibility). |
| V10 | **Audience MUST Match** | `audience` must match the Edge MCP's identity. |

### 3.3 Rejection Behavior

All rejections follow the LAW 0 rejection contract:

```json
{
  "error": "EXECUTION_AUTHORIZATION_REQUIRED",
  "detail": "EAT signature verification failed",
  "law0": true,
  "eat_id": "eat-...",
  "rule": "V1"
}
```

HTTP status codes:
- **403** — EAT missing, invalid, expired, or failed verification
- **402** — Payment required (x402, checked BEFORE EAT verification)
- **401** — No authentication credentials at all

Precedence: `401 (no auth) → 402 (no payment) → 403 (no/bad EAT) → 200 (success)`

---

## 4. Signing Strategy

### 4.1 Development / Staging

```
Provider: HMAC-SHA256
Key: EAT_SIGNING_KEY env var (separate from EI_SIGNING_KEY)
Verification: Shared secret (both gateways know the key)
```

### 4.2 Production

```
Provider: ECDSA P-256 via AWS KMS / Azure Key Vault / HashiCorp Vault
Inside MCP: Signs with private key (HSM-protected, never exported)
Edge MCP: Verifies with public key (distributed at deployment)
Non-repudiation: Guaranteed (Inside MCP cannot deny having issued an EAT)
```

### 4.3 Key Distribution

| Environment | Inside MCP | Edge MCP |
|-------------|-----------|----------|
| Dev (HMAC) | `EAT_SIGNING_KEY` in `.env` | Same `EAT_SIGNING_KEY` |
| Prod (ECDSA) | KMS key ARN / Vault key | Public key embedded in config or fetched from JWKS endpoint |

---

## 5. EAT Lifecycle

```
                    Inside MCP                    Edge MCP
                    ──────────                    ────────
                         │                            │
  Agent Request ────────►│                            │
                         │                            │
                    create_run()                      │
                    compile_run()                     │
                    contextualize_run()               │
                    govern_run()                      │
                         │                            │
                    ┌────┴────┐                       │
                    │ CONSULT │                       │
                    │ Sources │                       │
                    │ of Truth│                       │
                    └────┬────┘                       │
                         │                            │
                    commit_run()                      │
                    mint_execution_identity()         │
                    mint_eat()  ◄── NEW               │
                         │                            │
                    ┌────┴────────────────────────────►│
                    │        EAT (signed)             │
                    │                                 │
                    │                       verify_eat()
                    │                       check_nonce()
                    │                       enforce_scope()
                    │                                 │
                    │                       execute_side_effect()
                    │                                 │
                    │◄────────────────────────────────┤
                    │        Result + attestation      │
                    │                                 │
                    attest_run()                      │
                    record_to_ledger()                │
                         │                            │
```

### 5.1 EAT States

```
MINTED → DISPATCHED → VERIFIED → CONSUMED → EXPIRED
                  └→ REJECTED (failed verification)
                  └→ REVOKED  (revoked by Inside MCP)
```

---

## 6. x402 Payment Integration

x402 sits **outside** the EAT boundary. The payment is verified at the Edge **before** any EAT verification:

```
External Agent
      │
      │  POST /v1/exec  +  X-Payment header (x402 proof)
      ▼
  ┌─────────────────┐
  │ x402 Middleware  │  ← Verifies payment proof via facilitator
  │ (Edge)          │  ← Returns 402 if payment missing/invalid
  └────────┬────────┘
           │
  ┌────────▼────────┐
  │ Auth Middleware  │  ← Verifies API key / JWT
  │ (Edge)          │  ← Returns 401 if missing
  └────────┬────────┘
           │
  ┌────────▼────────┐
  │ EAT Verifier    │  ← Verifies EAT from Inside MCP
  │ (Edge)          │  ← Returns 403 if missing/invalid
  └────────┬────────┘
           │
  ┌────────▼────────┐
  │ Executor        │  ← Performs the actual side effect
  │ (Edge)          │
  └─────────────────┘
```

### 6.1 Who Pays Whom?

> "I don't need any 402-paying agents. I want my thing set up for 402.
> So those agents can come pay me. I'm not using agents to pay for anything."

The x402 integration is **merchant-side only**:
- External agents pay **you** (Veklom) to use your execution endpoints
- Your agents do NOT pay anyone — they are the product, not the consumer
- Payment flows: `External Agent → x402 Facilitator → Your EVM wallet`

---

## 7. Implementation Mapping to Existing Code

### 7.1 What Already Exists (Code Verified)

| Component | File | Status |
|-----------|------|--------|
| ExecutionIdentityV1 builder | `services/ei_builder.py` | ✅ Complete |
| 9-rule EI validation | `security/mcp_gateway.py` | ✅ Complete |
| Enterprise signing (KMS/Vault/Azure) | `services/enterprise_signer.py` | ✅ Complete |
| Canonical JSON + hash + sign | `services/canonical.py` | ✅ Complete |
| Governed orchestrator pipeline | `services/orchestrator.py` | ✅ Complete |
| PGL client (local DB) | `services/pgl_client.py` | ✅ Complete |
| Veklom PGL client (external API) | `services/veklom_pgl_client.py` | ✅ Complete |
| PGL adapter (local ↔ veklom) | `services/pgl_adapter.py` | ✅ Complete |
| x402 payment manager | `services/x402_payment.py` | ✅ Complete |
| Auth/Payment middleware | `security/middleware.py` | ✅ Complete |
| Audit service | `services/audit_service.py` | ✅ Complete |

### 7.2 What Needs to Be Built

| Component | Purpose | Priority |
|-----------|---------|----------|
| **EAT Builder** (`services/eat_builder.py`) | Mint EATs after EI minting. Uses the same signing infrastructure. | P0 |
| **EAT Verifier** (`security/eat_verifier.py`) | Verify EATs at the Edge. Nonce cache, signature check, expiry. | P0 |
| **EAT Model** (`models/execution_authorization.py`) | SQLAlchemy model for EAT persistence and replay detection. | P0 |
| **Edge Gateway** (`security/edge_gateway.py`) | Edge-side enforcement boundary (wrapper around EAT verifier). | P1 |
| **Orchestrator EAT step** | Add `mint_eat()` phase to `RunOrchestrator` after `mint_execution_identity()`. | P1 |
| **Nonce Cache** | In-memory TTL-bounded set (or Redis for multi-instance). | P1 |
| **JWKS/Public Key Endpoint** | Inside MCP exposes public key for Edge to fetch. | P2 |
| **EAT Admin Routes** | Revocation, listing, audit query. | P2 |

---

## 8. Configuration

### 8.1 New Environment Variables

```env
# --- EAT Signing (separate from EI signing) ---
EAT_SIGNING_PROVIDER=hmac          # hmac | aws | azure | vault
EAT_SIGNING_KEY=dev-insecure-eat-key  # HMAC only; production uses KMS
EAT_DEFAULT_TTL_SECONDS=300        # 5 minutes
EAT_MAX_TTL_SECONDS=600            # 10 minutes

# --- Edge MCP Identity ---
EDGE_MCP_IDENTITY=cappo-edge-mcp   # Audience field in EAT
INSIDE_MCP_IDENTITY=cappo-inside-mcp  # Issuer field in EAT

# --- Nonce Cache ---
EAT_NONCE_BACKEND=memory           # memory | redis
EAT_NONCE_REDIS_URL=redis://localhost:6379/1  # If redis backend
```

---

## 9. Security Considerations

### 9.1 Threat Model

| Threat | Mitigation |
|--------|------------|
| **Replay attack** | Nonce + single-use + short TTL (V3, V4, V8) |
| **Token theft** | Short TTL (300s default), single-use enforcement |
| **Forged EAT** | Cryptographic signature verification (V1) |
| **Scope escalation** | Scope is bound at mint time, Edge enforces (V6) |
| **Budget inflation** | Budget is capped at mint time from governance (V7) |
| **Expired token reuse** | Strict expiry check (V3) + nonce tracking within TTL window |
| **Inside MCP compromise** | Key rotation, HSM protection, audit logging (M7, M9) |
| **Edge MCP compromise** | Edge cannot mint; can only execute within EAT scope |

### 9.2 What Happens When...

| Scenario | Behavior |
|----------|----------|
| Edge receives request with no EAT | HTTP 403, `EXECUTION_AUTHORIZATION_REQUIRED` |
| Edge receives expired EAT | HTTP 403, `EAT_EXPIRED` |
| Edge receives EAT with bad signature | HTTP 403, `EAT_SIGNATURE_INVALID` |
| Edge receives replay (seen nonce) | HTTP 403, `EAT_REPLAY_DETECTED` |
| Inside MCP cannot reach Veklom | No EAT minted, request fails at governance stage |
| Agent trust score drops below 40 | Inside MCP refuses to mint EAT (M3) |
| Key rotation occurs | Outstanding EATs (< 5 min old) become invalid; clients retry |

---

## 10. Deployment Topology

### 10.1 Single-Process (Development)

Both gateways run in the same FastAPI process. EAT is still minted and verified (no shortcuts), but transport is in-memory function call rather than HTTP.

### 10.2 Split-Process (Production)

```
                Internet
                   │
           ┌───────▼───────┐
           │   Edge MCP    │  (Rust or Python, public-facing)
           │   Port 8443   │
           └───────┬───────┘
                   │ Internal network (mTLS)
           ┌───────▼───────┐
           │  Inside MCP   │  (Python/FastAPI, private)
           │   Port 8080   │
           └───────────────┘
```

### 10.3 Rust Edge (Target Architecture)

The Edge MCP is the candidate for Rust rewrite:
- High-throughput x402 payment verification
- Low-latency EAT signature verification
- Memory-safe nonce cache management
- The Inside MCP stays Python (complex governance logic, DB access)

---

## Appendix A: EAT vs EI Relationship

```
ExecutionIdentityV1 (EI)          Execution Authorization Token (EAT)
────────────────────────          ──────────────────────────────────
• Identity of the execution       • Authorization to execute
• "Who you are and what you       • "You may proceed with this
   were approved for"                specific action, right now"
• Long-lived (persisted in DB)    • Short-lived (300s TTL)
• Signed at mint time, immutable  • Single-use, consumed on execution
• Contains hash chain to PGL      • Contains EI execution_id as reference
• Created by orchestrator          • Created by Inside MCP after EI
• Validated by MCP Gateway        • Validated by Edge Gateway
```

The EI is the **proof of governance**. The EAT is the **authorization to act on that proof**.

---

## Appendix B: Testing Contract

Every rule in §3.1 and §3.2 must have a corresponding test:

```
tests/
  test_eat_builder.py       — M1-M9 (minting rules)
  test_eat_verifier.py      — V1-V10 (verification rules)
  test_eat_lifecycle.py     — Full lifecycle: mint → verify → consume → reject replay
  test_eat_edge_gateway.py  — Integration: Edge gateway + EAT verification
  test_eat_revocation.py    — Revocation propagation from Inside to Edge
```
