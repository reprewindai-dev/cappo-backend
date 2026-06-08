# cappo-backend

Greenfield CAPPO runtime backend — governed execution, PGL certificates, ExecutionIdentityV1, and LAW 0 enforcement.

## Status

**Pre-implementation.** This repo currently contains planning and specification documents only. No application code yet.

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

Seeded from analysis of [`reprewindai-dev/byosbackened`](https://github.com/reprewindai-dev/byosbackened) (the old `veklom-byos-backend`). See the migration mapping note for what carries forward and what does not.

## License

See [LICENSE](LICENSE).
