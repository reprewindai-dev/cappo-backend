# 00 — VEKLOM BIBLE — READ FIRST

**Mandatory context for every human or AI working in this repo.** The canonical operational source is `reprewindai-dev/veklom-ops-command/00_VEKLOM_BIBLE.md`.

Veklom is the **sovereign AI capability control plane / runtime authority layer**. It governs capability, not a permanent fleet of privileged agents.

**LAW 0:** No machine consequence without bounded authority and evidence.
**LAW 1:** Veklom itself may not bypass Veklom. (Veklom's own internal automations/agents must not hold ambient credentials to act directly on external systems; they must flow through the governed consequence architecture).

Canonical lifecycle: `Resolve → Bind policy/authority → Issue scoped grant → Instantiate ephemeral runtime → Execute → Record evidence → Revoke → Destroy → Observe/Settle when applicable`.

Truth order: **live behavior → Coolify runtime → GitHub default branch → verified PGL evidence → docs**. A merged PR, seeded fixture, screenshot, or pasted log is not automatically production proof.

Component boundaries: VCCP/UCH/UCR own capability lifecycle/orchestration; cAPI/Covenant owns governed connection/discovery; CAPPO owns fail-closed authorization; ABIDE owns blueprint/contract compilation; Lockerphycer owns secret/key security; BYOS is an execution substrate; Gnomledger/PGL owns evidence/provenance/lineage; VNP owns measurement/observation; RepoGate owns intake/security gating; Veklom ID owns identity evidence; x402 handles settlement where verified.

Standalone Veklom products may have their own UI. Inside Capability OS, reuse their underlying capabilities and build a Veklom-native surface; do not embed standalone pages wholesale.

Operations: GitHub is source truth; Coolify is deployment truth. Use Coolify UI/API for Coolify management and reserve SSH for direct host/container verification or operations. Internal ports such as `3000` and `8000` are valid behind Traefik; the old blanket port prohibition is retired. Never commit secrets or hard-code ephemeral Coolify container IDs.

Evidence labels: `VERIFIED_LIVE`, `VERIFIED_REPO`, `CONFIGURED`, `LAST_KNOWN`, `TARGET`, `UNVERIFIED`, `DEMO`, `ARCHIVED`. Do not claim compliance, hardware-enclave guarantees, immutability, or “100% production ready” without exact independent proof.

This Bible supersedes older Golden Bible, agent-alignment, topology, deployment-authority, and port-doctrine docs wherever they conflict.
