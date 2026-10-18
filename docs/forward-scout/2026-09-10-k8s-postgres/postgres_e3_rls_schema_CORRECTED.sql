-- ======================================================================
-- VEKLOM POSTGRES TARGET ADAPTER: E3 AUDIT + RLS SECURITY SCHEMA
-- CORRECTED FOR HOSTILE MULTI-TENANT USE
--
-- Status: DESIGN_VALID / REQUIRES REAL POSTGRES RERUN
-- ======================================================================

CREATE TABLE IF NOT EXISTS veklom_worker_tenant_binding (
    db_principal       NAME PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    worker_spiffe_id   TEXT NOT NULL,
    bound_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE veklom_worker_tenant_binding OWNER TO veklom_audit_owner;
REVOKE ALL ON TABLE veklom_worker_tenant_binding FROM PUBLIC;
REVOKE ALL ON TABLE veklom_worker_tenant_binding FROM veklom_worker_role;
REVOKE ALL ON TABLE veklom_worker_tenant_binding FROM veklom_target_adapter_role;
REVOKE ALL ON TABLE veklom_worker_tenant_binding FROM veklom_e3_observer_role;

CREATE OR REPLACE FUNCTION veklom_bound_tenant()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT b.tenant_id
      FROM public.veklom_worker_tenant_binding AS b
     WHERE b.db_principal = session_user
$$;

ALTER FUNCTION veklom_bound_tenant() OWNER TO veklom_audit_owner;
REVOKE ALL ON FUNCTION veklom_bound_tenant() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION veklom_bound_tenant() TO veklom_worker_role;

CREATE OR REPLACE FUNCTION veklom_bound_worker_spiffe()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT b.worker_spiffe_id
      FROM public.veklom_worker_tenant_binding AS b
     WHERE b.db_principal = session_user
$$;

ALTER FUNCTION veklom_bound_worker_spiffe() OWNER TO veklom_audit_owner;
REVOKE ALL ON FUNCTION veklom_bound_worker_spiffe() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION veklom_bound_worker_spiffe() TO veklom_worker_role;

CREATE TABLE IF NOT EXISTS veklom_consequence_audit (
    transition_id          TEXT PRIMARY KEY,
    request_commitment     TEXT NOT NULL,
    authority_digest       TEXT NOT NULL,
    execution_id           TEXT NOT NULL,
    attempt_id             TEXT NOT NULL,
    lease_id               TEXT NOT NULL,
    tenant_id              TEXT NOT NULL,
    worker_spiffe_id       TEXT NOT NULL,
    target_resource        TEXT NOT NULL,
    mutation_digest        TEXT NOT NULL CHECK (mutation_digest ~ '^[0-9a-f]{64}$'),
    effect_commitment      TEXT NOT NULL,
    target_transaction_id  TEXT NOT NULL,
    affected_rows          INTEGER NOT NULL CHECK (affected_rows >= 0),
    recorded_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE veklom_consequence_audit OWNER TO veklom_audit_owner;
ALTER TABLE veklom_consequence_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE veklom_consequence_audit FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE veklom_consequence_audit FROM PUBLIC;
REVOKE ALL ON TABLE veklom_consequence_audit FROM veklom_worker_role;
REVOKE ALL ON TABLE veklom_consequence_audit FROM veklom_target_adapter_role;
REVOKE ALL ON TABLE veklom_consequence_audit FROM veklom_e3_observer_role;

GRANT SELECT ON TABLE veklom_consequence_audit TO veklom_worker_role;

DROP POLICY IF EXISTS veklom_worker_select_own_tenant ON veklom_consequence_audit;
CREATE POLICY veklom_worker_select_own_tenant
    ON veklom_consequence_audit
    FOR SELECT
    TO veklom_worker_role
    USING (
        tenant_id = veklom_bound_tenant()
        AND worker_spiffe_id = veklom_bound_worker_spiffe()
    );

GRANT INSERT, SELECT ON TABLE veklom_consequence_audit TO veklom_target_adapter_role;

DROP POLICY IF EXISTS veklom_target_adapter_insert ON veklom_consequence_audit;
CREATE POLICY veklom_target_adapter_insert
    ON veklom_consequence_audit
    FOR INSERT
    TO veklom_target_adapter_role
    WITH CHECK (true);

DROP POLICY IF EXISTS veklom_target_adapter_readback ON veklom_consequence_audit;
CREATE POLICY veklom_target_adapter_readback
    ON veklom_consequence_audit
    FOR SELECT
    TO veklom_target_adapter_role
    USING (true);

GRANT SELECT ON TABLE veklom_consequence_audit TO veklom_e3_observer_role;

DROP POLICY IF EXISTS veklom_observer_e3_reconciliation ON veklom_consequence_audit;
CREATE POLICY veklom_observer_e3_reconciliation
    ON veklom_consequence_audit
    FOR SELECT
    TO veklom_e3_observer_role
    USING (true);

CREATE UNIQUE INDEX IF NOT EXISTS idx_veklom_audit_request_commitment
    ON veklom_consequence_audit (request_commitment);

CREATE INDEX IF NOT EXISTS idx_veklom_audit_execution_attempt
    ON veklom_consequence_audit (execution_id, attempt_id);

CREATE INDEX IF NOT EXISTS idx_veklom_audit_tenant_recorded
    ON veklom_consequence_audit (tenant_id, recorded_at);

-- Atomic E3 protocol requirement:
-- BEGIN;
--   <business mutation>;
--   INSERT INTO veklom_consequence_audit (...same transition...);
-- COMMIT;
-- If either statement fails, the transaction MUST roll back.
