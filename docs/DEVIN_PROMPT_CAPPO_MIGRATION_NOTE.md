# Devin Prompt — CAPPO Migration Mapping Note

This is a documentation-only task for the implementation agent.

## Goal
Create a migration mapping note from `veklom-byos-backend` to the new CAPPO backend.

## Required output
For each of these old backend areas:
- PGL model
- PGL client
- orchestrator
- MCP gateway
- exec router
- audit logging
- middleware / payment gate

Document:
1. what exists today
2. what is still valid conceptually
3. what must not be carried forward
4. what maps into the new CAPPO backend structure
5. what can be retired after migration

## Constraints
- no code changes
- no doc changes to canonical doctrine
- this note is for migration planning only
