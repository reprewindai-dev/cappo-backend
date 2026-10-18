---
name: api-endpoint-extension
description: Workflow command scaffold for api-endpoint-extension in cappo-backend.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /api-endpoint-extension

Use this workflow when working on **api-endpoint-extension** in `cappo-backend`.

## Goal

Adds or updates API endpoints, often including router files, service logic, and associated tests.

## Common Files

- `cappo_backend/api/routers/*.py`
- `cappo_backend/services/*.py`
- `cappo_backend/main.py`
- `tests/*.py`
- `cappo_backend/tests/integration/*.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit or create files in cappo_backend/api/routers/ (e.g., exec_router.py, interlink.py, mcp.py, vnp_router.py, etc.)
- Update or create related service files in cappo_backend/services/ as needed
- Update cappo_backend/main.py if new routers are registered or main logic changes
- Add or update integration/unit tests in tests/ or cappo_backend/tests/integration/
- Update documentation if required

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.