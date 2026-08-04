---
name: security-or-middleware-fix
description: Workflow command scaffold for security-or-middleware-fix in cappo-backend.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /security-or-middleware-fix

Use this workflow when working on **security-or-middleware-fix** in `cappo-backend`.

## Goal

Restores or updates security/middleware modules to fix regressions or improve functionality.

## Common Files

- `cappo_backend/security/*.py`
- `cappo_backend/security/middleware.py`
- `cappo_backend/api/routers/*.py`
- `cappo_backend/services/orchestrator.py`
- `cappo_backend/main.py`
- `tests/test_middleware.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit or restore files in cappo_backend/security/ or cappo_backend/security/middleware.py
- Update imports in dependent modules (e.g., routers, orchestrator, main.py)
- Add or update relevant tests (e.g., tests/test_middleware.py, tests/test_auth_middleware.py)
- Verify application startup and test pass

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.