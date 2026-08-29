# Veklom N8N Strategy

**Date:** 2026-08-28
**Status:** `TARGET_ARCHITECTURE / NOT_RUNTIME_VERIFIED`

This document describes a target integration boundary. It does **not** assert that n8n is currently deployed, healthy, authoritative, or fully governed in production.

## Architectural Boundary

The intended model is to use n8n as an execution ecosystem while preserving Veklom—identity, policy, CAPPO authority, and durable evidence—as the authority layer.

### Target responsibilities

- Experience layer: Veklom UI/API and human or machine intent.
- Authority layer: Veklom identity, policy, CAPPO, leases/attenuation, evidence, and offline authority.
- Execution ecosystem: n8n, MCP, BYOS, Ollama, external APIs, SaaS, and other replaceable executors.

### Observed current responsibilities

Source code contains CAPPO governance and execution-provider work plus n8n-related integration artifacts. That observation is not equivalent to proving that every n8n consequence is currently dominated by CAPPO at runtime.

`verified_runtime_state = NOT_VERIFIED`

## Target integration

A `VeklomN8nExecutionProvider`-style adapter may translate a governed execution envelope into n8n requests under attenuated, short-lived authority. The security invariant is that n8n itself must not be able to mint or widen authority, and any consequential target must independently enforce the Veklom authorization boundary.

That invariant remains a target until negative bypass tests, replay/expiry tests, deployed-source identity, canonical listener/routing checks, and durable Gnomledger/PGL evidence have all been independently demonstrated.

## Licensing boundary

The intended deployment model is self-hosted n8n Community Edition where appropriate, with Veklom identity/policy/evidence state remaining external so that n8n can be replaced by another executor if required. This is architectural intent, not a statement about current commercial rights, production deployment status, or an executed licensing agreement.
