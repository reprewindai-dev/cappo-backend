# PostgreSQL E3/RLS Evidence Review - 2026-09-10

## Verdict

The supplied files are useful forward-scout material, but the current `VALID_CERTIFIED` / `RLS_ISOLATED_VERIFIED` labels are **not yet supported by the attached evidence**.

## Defects that must be corrected before sealing

1. **Tenant mismatch invalidates the read-attack proof.**  
   The RLS violation file attacks `tenant-prod-01` -> `tenant-prod-02`, while the supplied E3 audit log contains `tenant-enterprise-prod-01` and `tenant-enterprise-prod-02`. A `0 rows` result against a tenant value not shown to exist does not prove RLS filtering. The rerun must first establish a positive control: observer sees the exact target row, then worker sees zero rows.

2. **The recorded INSERT attack is not valid SQL.**  
   `INSERT INTO veklom_consequence_audit (tenant_id = 'tenant-prod-02')` is syntactically malformed. A claimed SQLSTATE `42501` cannot be accepted as proof for the statement as recorded. Capture the exact executable INSERT and the exact server error from PostgreSQL.

3. **The schema's worker `FOR ALL` policy contradicts an immutable audit trail.**  
   `FOR ALL` plus worker DML privileges can permit own-tenant UPDATE/DELETE unless separately revoked. The corrected design gives workload roles SELECT-only access and reserves authoritative receipt writes for a server-held target-adapter role.

4. **`app.current_tenant_id` is not a safe trust anchor for a compromised raw-SQL worker.**  
   A worker-controlled session setting cannot be the sole tenant-security boundary if the attacker can issue arbitrary SQL. The corrected design derives tenant context from a protected mapping keyed by `session_user`.

5. **`execution_id` must not be the global consequence identity.**  
   The supplied schema uses `execution_id UUID PRIMARY KEY`. The corrected design uses `transition_id` as the primary consequence identity and preserves `execution_id` plus `attempt_id` as execution context.

6. **The first supplied mutation digest is the SHA-256 digest of an empty byte string.**  
   `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` cannot, without additional explanation and raw payload evidence, substantiate a non-empty mutated-row commitment.

7. **Atomic E3 finality is not proven by the schema or JSON alone.**  
   The business mutation and consequence-audit insertion must be shown committing in the same PostgreSQL transaction. A deterministic failure between them must roll both back.

## Rerun classification

Current artifacts: `DESIGN_VALID / EVIDENCE_INSUFFICIENT_FOR_SEAL`

Required rerun outputs:
- exact PostgreSQL version and role attributes;
- exact table owner and RLS/force-RLS state;
- positive-control observer query showing the target tenant row exists;
- worker cross-tenant query returning zero for that exact row;
- exact executable hostile DML and server SQLSTATE/body;
- observer read-only mutation failures;
- tenant-context spoof attempt;
- atomic rollback and atomic commit trials;
- transition/request/effect commitments and target transaction identity;
- raw stdout/stderr or structured DB-driver results with timestamps and hash.
