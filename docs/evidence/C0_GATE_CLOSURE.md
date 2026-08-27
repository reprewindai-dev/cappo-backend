# C0 Gate — Canonical Binding Record

| Field | Value |
|---|---|
| Gate | C0 — Canonical Binding Achieved |
| Tag | `veklom-p5-closure-v1` |
| Binding SHA | `b48007614bee92d1caacc628d96fe9a786e8cd47` |
| Date | 2026-08-27 |
| Author | Antigravity Agent |

## Evidence Summary

| Layer | Claim | Result |
|---|---|---|
| Runtime | 75/75 adversarial hostile tests | ✅ PASS (clean state, verified twice) |
| Formal | TLC model check | ✅ 2,752 states, 576 distinct, depth 5, 0 violations |
| Correspondence | Python ↔ TLA+ mutation surface | ✅ 3 sites → 7 actions, AST-verified, no unmapped routes |
| P3 | No Biscuit → DENY enforced end-to-end | ✅ Tests require real tokens |

## Three Canonical Commits

| Commit | SHA | Contents |
|---|---|---|
| Runtime | `0587031` | P3/P4/P5 engine, service, ConsequenceExecution, CapabilityActionReceipt, `formal/p5_correspondence.py` |
| Evidence | `fe98ef3` | 75 adversarial tests, TLA+ formal specs, all test families |
| Registry | `b480076` | `docs/evidence/` — 4 canonical closure docs, junk deleted |

## Preserved Caveats (Immutable)

- Liveness not formally claimed
- G0B.2 (SVID mTLS) deferred
- G1 (WAN-OFF) deferred
- `NoIllegalTruthTransition` proves state membership, not full edge history — flagged for future hardening; does **not** reopen P5

## Closure Statement

The P5 boundary is closed from SHA `b48007614bee92d1caacc628d96fe9a786e8cd47` forward.
This file is the permanent on-chain record of the C0 gate event.
