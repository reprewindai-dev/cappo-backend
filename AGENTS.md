# AGENTS.md — READ FIRST

Before any work, read [`00_VEKLOM_BIBLE.md`](./00_VEKLOM_BIBLE.md).

That file is the canonical Veklom cross-repo architecture/runtime contract. Repo-local source and tests govern CAPPO implementation details only when they do not conflict with current runtime evidence or the Bible.

Do not infer service placement, ports, health, compliance, or production status from old docs, and never use historical hosting instructions as current deployment authority. Consult the latest verified deployment record (`veklom-m1p2/backups/prod-upgrade-*/DEPLOY-RECORD.md`) before changing infrastructure.
- **Current deployment, verified 2026-10-08:** Docker Desktop (WSL2) on the owner's Windows host, published through a Cloudflare tunnel.
- **Owner decision (2026-10-07):** Coolify, Hetzner and Vercel are not part of Veklom. Older docs that route work to them are stale.

Veklom's design stays portable (Own Your Cloud). The hosting above is today's deployment, not the architecture.

## CAPPO's role, and the three meanings of "sandbox"

CAPPO is the sole consequence authority: one bounded, single-use grant for one exact operation, with a signed receipt. Veklom is one product. The design model is in veklom-FRONTEND `docs/capability-os/DESIGN_MODEL.md`. Keep these apart:

- **Customer playground** (Capability OS "Switch to sandbox"): mounts carry `execution_scope.project = "sandbox"`.
  - It follows the same rules and code paths as live, and must never change live state.
  - A target adapter that keeps no separate playground copy (`project_isolated` is not True) must refuse playground consequences (`target_has_no_sandbox`), never execute them against live data.
- **Execution containment** (LockerPhycer): what a running agent can reach. It applies in playground and live alike. Going live never removes containment.
- **Deployment test copy** (staging): tests releases of Veklom itself. It is not a customer feature.
