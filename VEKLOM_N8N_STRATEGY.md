# Veklom N8N Strategy

**Date:** 2026-08-28
**Status:** ACTIVE
**Pivot Context:** We took everything off Hetzner. The strategy is moving to local, BYOS, sovereign node execution.

## The Architectural Boundary
We are adopting n8n as the **execution ecosystem** and preserving Veklom (CAPPO/PGL) as the **authority layer**.

We are NOT:
- Competing with n8n by building a workflow editor, scheduler, or basic integration nodes.
- Attempting to be a basic automation company.

We ARE:
- Placing n8n behind the CAPPO boundary.
- Governing all of n8n's consequences.
- Proving that even if n8n bypasses the authorized envelope, Veklom intercepts and denies the action.

## 3-Layer Architecture
1. **Experience Layer**: Veklom UI, API, Human Intent.
2. **Authority Layer**: VEKLOM (Identity, Policy, CAPPO, Leases, Evidence, Offline Authority).
3. **Execution Ecosystem**: n8n, MCP, BYOS, Ollama, APIs, SaaS.

## Implementation: `VeklomN8nExecutionProvider`
A clean adapter translates Veklom's `ExecutionEnvelope` into n8n payloads, ensuring n8n executes under an attenuated, short-lived authority.
If n8n attempts to make an unauthorized HTTP call or access restricted MCP resources, the target resource will be bound by Veklom validation and reject the action.

## Licensing Boundary (Important)
- We use self-hosted Community Edition initially.
- Veklom state (Identity, Policy, Evidence) MUST remain external to n8n so we can swap out n8n for Temporal or native runners if licensing issues arise.
- We do not silently embed n8n as a commercial white-label SaaS offering without a commercial agreement.
