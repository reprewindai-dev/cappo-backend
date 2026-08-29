# N8N-13 Release Evidence — QUARANTINED

**Status:** `UNVERIFIED_CLAIM_SET`

This document previously asserted a completed CAPPO → n8n → governed-target → persistence path using concrete execution identifiers, hashes, runtime ports, settlement claims, and a formal PASS/Verified label. Those assertions are not accepted as canonical Veklom evidence.

The prior record is quarantined because it does not, by itself, establish all required verification conditions: exact deployed source SHA, canonical service listeners, protocol identity, independent HTTP health, routing identity, durable Gnomledger evidence, replay-safe authority binding, or independently reproducible negative/positive tests.

In particular, examples using ports `3000` or `8000` conflict with the current foundation runtime contract and must not be treated as production truth.

## Required evidence before promotion

A future release-evidence record may be promoted only when it is generated from a reproducible test run and contains references to durable evidence rather than invented or manually transcribed receipts. At minimum it must establish:

- CAPPO canonical listener `8002` and expected protocol identity;
- the actual deployed commit/image identity;
- the governed target identity and authority boundary used for the test;
- replay/expiry/attenuation negative tests;
- durable Gnomledger/PGL evidence that can be independently correlated to the execution;
- explicit distinction between simulated/local test effects and production consequences;
- no unsupported payment, settlement, convergence, latency, or success claims.

Until those conditions are met:

`verified_runtime_state = NOT_VERIFIED`

`unverified_claims = [N8N-13 end-to-end completion, settlement success, restart recovery, durable evidence persistence]`
