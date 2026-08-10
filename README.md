> [!IMPORTANT]
> **VEKLOM BIBLE — READ FIRST:** [`00_VEKLOM_BIBLE.md`](./00_VEKLOM_BIBLE.md)
> It supersedes older cross-repo architecture/deployment/alignment claims wherever they conflict. Verify runtime state before production claims.

# cappo-backend

Greenfield CAPPO runtime backend — governed execution, PGL certificates, ExecutionIdentityV1, and LAW 0 enforcement.

## Status

**Phase 1 — governance kernel implemented.** Forward-constructed from the lineage
seeds documented in the migration mapping note (not a port of the old backend,
whose PGL/orchestrator/MCP-gateway objects do not exist as code).

## Architecture (Phase 1)

```
POST /v1/exec  ──►  RunOrchestrator (single governed entry path, no bypass)
                      create → compile → contextualize → govern → commit
                        → [mint ExecutionIdentityV1] → route → execute → attest
                                       │
                  PGLClient ───────────┤  mints + persists PGLCertificate (+ ledger)
                  ExecutionIdentityBuilder  canonical JSON → SHA-256 hash → Ed25519 signature
                  MCPGateway.require_execution_identity()  9-rule LAW 0 enforcement (403)
                  AuditService  hash-chained, fail-loud, law0_violation events
```

| Component | Module |
|---|---|
| Config (incl. `CAPPO_REQUIRE_PERSISTENT_PGL`, `PGL_LEDGER_URL`) | `cappo_backend/config.py` |
| Data models (`PGLCertificate`, `PGLLedgerEvent`, `ExecutionIdentity`, `GovernedRun`, `AuditEvent`) | `cappo_backend/models/` |
| Canonical hash/sign helpers | `cappo_backend/services/canonical.py` |
| ExecutionIdentityV1 builder | `cappo_backend/services/ei_builder.py` |
| PGL client (+ gnomledger forwarding) | `cappo_backend/services/pgl_client.py` |
| Run state machine | `cappo_backend/services/run_state.py` |
| Orchestrator (audit all rejections) | `cappo_backend/services/orchestrator.py` |
| Audit/ledger service (forwarding) | `cappo_backend/services/audit_service.py` |
| MCP gateway (LAW 0 enforcement) | `cappo_backend/security/mcp_gateway.py` |
| Governed `/v1/exec` route | `cappo_backend/api/routers/exec_router.py` |
| Queryable Audit API | `cappo_backend/api/routers/audit_router.py` |

## Quickstart

```bash
pip install -e ".[dev]"
alembic upgrade head          # create schema
pytest                        # run the test suite
uvicorn cappo_backend.main:app --reload --ws none
```

Production must set `CAPPO_REQUIRE_PERSISTENT_PGL=true` and a real `EI_SIGNING_KEY`.

Install `.[evm]` only if you need the optional EVM signing / settlement stack.

## Coolify Provider Wiring

CAPPO governed execution enters through `/v1/exec`. OpenAI/Groq use the
OpenAI-compatible provider adapter. Ollama uses CAPPO's native Ollama adapter so
model lifecycle controls are transmitted on the provider request itself.

For production Ollama on its own server, set these Coolify variables:

```env
EXECUTOR_MODE=openai
LLM_PROVIDER_NAME=ollama
LLM_MODEL=qwen2.5:3b
LLM_TIMEOUT_SECONDS=60
OLLAMA_BASE_URL=http://YOUR_OLLAMA_SERVER_PRIVATE_IP:11434
```

For `LLM_PROVIDER_NAME=ollama`, CAPPO sends `POST /api/chat` and includes
`keep_alive: 0` plus `stream: false`. `OLLAMA_BASE_URL` should identify the
Ollama server root. If an older deployment still supplies an explicit CAPPO URL
ending in `/v1`, the native adapter strips that suffix before calling `/api/chat`:

```env
LLM_BASE_URL=http://YOUR_OLLAMA_SERVER_PRIVATE_IP:11434/v1
```

Do not configure a proxy/allowlist that exposes only `/v1/chat/completions` for
this adapter; CAPPO must be able to reach Ollama's native `/api/chat` endpoint.

Do not use `localhost` in Coolify unless Ollama runs in the same container.
Inside Docker, `localhost` is the CAPPO container itself, not the host or a
separate Ollama server.

## Documentation

All planning docs live in [`docs/`](docs/):

| Document | Purpose |
|---|---|
| `CAPPO_MIGRATION_MAPPING_NOTE.md` | Migration mapping from `veklom-byos-backend` — what exists, what carries forward, what must not |
| `CAPPO_EI_LINEAGE_AND_GAP_BRIEF.md` | ExecutionIdentityV1 lineage anchors and confirmed LAW 0 gaps |
| `CAPPO_EXECUTIONIDENTITYV1_IMPLEMENTATION_PLAN.md` | Phase 1 implementation plan — mint point, fields, validation, persistence |
| `DEVIN_PROMPT_CAPPO_SPEC.md` | Agent prompt for the EI specification phase |
| `DEVIN_PROMPT_CAPPO_PHASE1_IMPLEMENTATION.md` | Agent prompt for Phase 1 implementation |
| `DEVIN_PROMPT_CAPPO_MIGRATION_NOTE.md` | Agent prompt for the migration mapping task |

## Lineage

Seeded from analysis of [`reprewindai-dev/veklom-byos-backend`](https://github.com/reprewindai-dev/veklom-byos-backend) (the old `veklom-byos-backend`). See the migration mapping note for what carries forward and what does not.

## License

See [LICENSE](LICENSE).
