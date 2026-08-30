# AGENTS.md — READ FIRST

Before any work, read [`00_VEKLOM_BIBLE.md`](./00_VEKLOM_BIBLE.md).

That file is the canonical Veklom cross-repo architecture/runtime contract. Repo-local source and tests govern CAPPO implementation details only when they do not conflict with current runtime evidence or the Bible.

Do not infer service placement, ports, health, compliance, or production status from old docs. Use Coolify UI/API/MCP for Coolify management; SSH is for direct host/container verification or operations. Host port `8000` is currently Coolify-owned even though internal Docker port `8000` can be used behind Traefik.
