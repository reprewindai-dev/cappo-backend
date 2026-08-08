---
name: service-logic-update
description: Workflow command scaffold for service-logic-update in cappo-backend.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /service-logic-update

Use this workflow when working on **service-logic-update** in `cappo-backend`.

## Goal

Implements or modifies backend service logic, often in response to feature or bugfix requirements.

## Common Files

- `cappo_backend/services/*.py`
- `cappo_backend/models/*.py`
- `cappo_backend/config.py`
- `tests/*.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit or create files in cappo_backend/services/
- Update related models or config files if needed
- Add or update tests for the service logic
- Update documentation if required

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.