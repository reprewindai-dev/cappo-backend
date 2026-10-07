-- Veklom PostgreSQL E3/RLS schema v2
-- Status: DESIGN_VALID / REQUIRES REAL POSTGRESQL RERUN
-- Scope: clean installation only. This file intentionally does not use
-- CREATE TABLE IF NOT EXISTS because the supplied v1 table is incompatible.
-- Write and test a reviewed migration before applying this to an existing DB.
-- Required pre-existing NOLOGIN group roles:
--   veklom_audit_owner, veklom_worker_role,
--   veklom_target_adapter_role, veklom_e3_observer_role
-- Required deployment rule: every tenant workload connection uses a distinct
-- LOGIN principal mapped below. Do not multiplex tenants through one DB login.

BEGIN;

CREATE SCHEMA veklom_e3 AUTHORIZATION veklom_audit_owner;
REVOKE ALL ON SCHEMA veklom_e3 FROM PUBLIC;
GRANT USAGE ON SCHEMA veklom_e3 TO
    veklom_worker_role,
    veklom_target_adapter_role,
    veklom_e3_observer_role;

CREATE TABLE veklom_e3.worker_tenant_binding (
    db_principal       NAME PRIMARY KEY,
    tenant_id          TEXT NOT NULL CHECK (tenant_id <> ''),
    worker_spiffe_id   TEXT NOT NULL CHECK (worker_spiffe_id <> ''),
    bound_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (db_principal, tenant_id, worker_spiffe_id)
);
ALTER TABLE veklom_e3.worker_tenant_binding OWNER TO veklom_audit_owner;
REVOKE ALL ON TABLE veklom_e3.worker_tenant_binding FROM PUBLIC;

-- SECURITY DEFINER is needed because workers cannot read the binding table.
-- Every name is schema-qualified and pg_temp is explicitly last.
CREATE FUNCTION veklom_e3.bound_tenant()
RETURNS TEXT
LANGUAGE sql
STABLE
PARALLEL RESTRICTED
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
    SELECT b.tenant_id
      FROM veklom_e3.worker_tenant_binding AS b
     WHERE b.db_principal = session_user
$function$;
ALTER FUNCTION veklom_e3.bound_tenant() OWNER TO veklom_audit_owner;
REVOKE ALL ON FUNCTION veklom_e3.bound_tenant() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION veklom_e3.bound_tenant() TO veklom_worker_role;

CREATE FUNCTION veklom_e3.bound_worker_spiffe()
RETURNS TEXT
LANGUAGE sql
STABLE
PARALLEL RESTRICTED
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
    SELECT b.worker_spiffe_id
      FROM veklom_e3.worker_tenant_binding AS b
     WHERE b.db_principal = session_user
$function$;
ALTER FUNCTION veklom_e3.bound_worker_spiffe() OWNER TO veklom_audit_owner;
REVOKE ALL ON FUNCTION veklom_e3.bound_worker_spiffe() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION veklom_e3.bound_worker_spiffe() TO veklom_worker_role;

CREATE TABLE veklom_e3.consequence_receipt (
    transition_id          TEXT PRIMARY KEY CHECK (transition_id <> ''),
    request_commitment     TEXT NOT NULL UNIQUE
        CHECK (request_commitment ~ '^[0-9a-f]{64}$'),
    authority_digest       TEXT NOT NULL
        CHECK (authority_digest ~ '^[0-9a-f]{64}$'),
    execution_id           TEXT NOT NULL CHECK (execution_id <> ''),
    attempt_id             TEXT NOT NULL CHECK (attempt_id <> ''),
    lease_id               TEXT NOT NULL CHECK (lease_id <> ''),
    tenant_id              TEXT NOT NULL CHECK (tenant_id <> ''),
    worker_spiffe_id       TEXT NOT NULL CHECK (worker_spiffe_id <> ''),
    target_resource        TEXT NOT NULL CHECK (target_resource <> ''),
    mutation_digest        TEXT NOT NULL
        CHECK (mutation_digest ~ '^[0-9a-f]{64}$'),
    effect_commitment      TEXT NOT NULL
        CHECK (effect_commitment ~ '^[0-9a-f]{64}$'),
    target_transaction_id  TEXT NOT NULL
        DEFAULT pg_current_xact_id()::TEXT,
    affected_rows          BIGINT NOT NULL CHECK (affected_rows >= 0),
    recorded_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (execution_id, attempt_id)
);
ALTER TABLE veklom_e3.consequence_receipt OWNER TO veklom_audit_owner;
ALTER TABLE veklom_e3.consequence_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE veklom_e3.consequence_receipt FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE veklom_e3.consequence_receipt FROM PUBLIC;
GRANT SELECT ON TABLE veklom_e3.consequence_receipt TO veklom_worker_role;
GRANT INSERT, SELECT ON TABLE veklom_e3.consequence_receipt
    TO veklom_target_adapter_role;
GRANT SELECT ON TABLE veklom_e3.consequence_receipt
    TO veklom_e3_observer_role;

CREATE POLICY worker_select_own_receipts
    ON veklom_e3.consequence_receipt
    FOR SELECT TO veklom_worker_role
    USING (
        tenant_id = veklom_e3.bound_tenant()
        AND worker_spiffe_id = veklom_e3.bound_worker_spiffe()
    );

CREATE POLICY adapter_insert_receipts
    ON veklom_e3.consequence_receipt
    FOR INSERT TO veklom_target_adapter_role
    WITH CHECK (true);

CREATE POLICY adapter_readback_receipts
    ON veklom_e3.consequence_receipt
    FOR SELECT TO veklom_target_adapter_role
    USING (true);

CREATE POLICY observer_read_all_receipts
    ON veklom_e3.consequence_receipt
    FOR SELECT TO veklom_e3_observer_role
    USING (true);

CREATE INDEX consequence_receipt_tenant_recorded_idx
    ON veklom_e3.consequence_receipt (tenant_id, recorded_at);

-- The adapter must insert this receipt in the same transaction as the
-- business mutation. This schema cannot by itself prove that the adapter did
-- so, that commitments describe the real row bytes, or that CAPPO authorized
-- the request. Those are rerun evidence requirements.

COMMIT;
