-- Governance and audit tables used by later workflows (agents, approvals, ML, evaluation),
-- created now so every workflow shares one schema and one migration history.

CREATE TABLE ops.investigation_case (
    case_id      text PRIMARY KEY,
    invoice_id   text REFERENCES ops.invoice,
    opened_by    text NOT NULL,
    opened_at    timestamptz NOT NULL DEFAULT now(),
    question     text,
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'awaiting_approval', 'closed')),
    risk_level   text NOT NULL DEFAULT 'low' CHECK (risk_level IN ('low', 'medium', 'high'))
);

CREATE TABLE ops.case_evidence (
    evidence_id text PRIMARY KEY,
    case_id     text NOT NULL REFERENCES ops.investigation_case,
    kind        text NOT NULL CHECK (kind IN ('sql', 'document', 'policy', 'model_score', 'erp')),
    reference   text NOT NULL,          -- query id, document id + page, model run id, ...
    sha256      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ops.approval (
    approval_id  text PRIMARY KEY,
    case_id      text REFERENCES ops.investigation_case,
    invoice_id   text REFERENCES ops.invoice,
    action       text NOT NULL,
    requested_by text NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'escalated')),
    decided_by   text,
    decided_at   timestamptz,
    reason       text,
    CHECK ((status = 'pending') = (decided_by IS NULL))
);

CREATE TABLE ops.model_run (
    run_id      text PRIMARY KEY,
    model_name  text NOT NULL,
    version     text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    params      jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics     jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Append-only audit log, range-partitioned by month so old partitions can be detached/archived.
CREATE TABLE ops.audit_event (
    event_id   bigint GENERATED ALWAYS AS IDENTITY,
    event_time timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL,
    action     text NOT NULL,
    entity     text NOT NULL,
    entity_id  text,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (event_id, event_time)
) PARTITION BY RANGE (event_time);

CREATE TABLE ops.audit_event_default PARTITION OF ops.audit_event DEFAULT;

-- Creates (idempotently) one monthly partition. Call from a scheduled job before each month starts.
CREATE OR REPLACE FUNCTION ops.ensure_audit_partition(month_start date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    part text := format('audit_event_%s', to_char(month_start, 'YYYY_MM'));
BEGIN
    IF to_regclass(format('ops.%I', part)) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE ops.%I PARTITION OF ops.audit_event FOR VALUES FROM (%L) TO (%L)',
            part, month_start, (month_start + interval '1 month')::date);
    END IF;
    RETURN part;
END $$;

-- Audit rows are append-only: block UPDATE and DELETE (TRUNCATE needs ownership anyway).
CREATE OR REPLACE FUNCTION ops.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ops.audit_event is append-only';
END $$;

CREATE TRIGGER audit_event_no_update BEFORE UPDATE OR DELETE ON ops.audit_event
    FOR EACH ROW EXECUTE FUNCTION ops.audit_immutable();

-- Row-level security demo: analysts see only invoices of suppliers they are assigned to.
CREATE TABLE ops.user_supplier_access (
    user_name   text NOT NULL,
    supplier_id text NOT NULL REFERENCES ops.supplier,
    PRIMARY KEY (user_name, supplier_id)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_analyst') THEN
        CREATE ROLE ops_analyst NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_finance_admin') THEN
        CREATE ROLE ops_finance_admin NOLOGIN;
    END IF;
END $$;

ALTER TABLE ops.invoice ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.invoice FORCE ROW LEVEL SECURITY;

CREATE POLICY invoice_admin_all ON ops.invoice FOR ALL TO ops_finance_admin USING (true);
CREATE POLICY invoice_analyst_assigned ON ops.invoice FOR SELECT TO ops_analyst
    USING (supplier_id IN (
        SELECT usa.supplier_id FROM ops.user_supplier_access usa
        WHERE usa.user_name = current_setting('app.user', true)));
-- The table owner / migration role keeps full access for loads and maintenance.
CREATE POLICY invoice_owner_all ON ops.invoice FOR ALL TO CURRENT_USER USING (true);
