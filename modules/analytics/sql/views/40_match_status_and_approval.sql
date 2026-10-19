-- Invoice-level match status: "matched" means no error-level exception.
CREATE VIEW ops.v_invoice_match_status AS
SELECT
    i.invoice_id,
    i.supplier_id,
    i.invoice_date,
    i.total,
    count(e.exception_code) FILTER (WHERE e.severity = 'error') AS n_errors,
    count(e.exception_code) FILTER (WHERE e.severity = 'warning') AS n_warnings,
    count(e.exception_code) FILTER (WHERE e.severity = 'info') AS n_info,
    CASE
        WHEN count(e.exception_code) FILTER (WHERE e.severity = 'error') = 0
            THEN 'matched'
        ELSE 'exception'
    END AS match_status
FROM ops.invoice AS i
LEFT JOIN ops.v_invoice_exceptions AS e ON i.invoice_id = e.invoice_id
WHERE i.status <> 'rejected'
GROUP BY i.invoice_id, i.supplier_id, i.invoice_date, i.total;

-- Management chain with approval limits (recursive CTE, depth-guarded against
-- bad data).
CREATE OR REPLACE FUNCTION ops.approval_chain(start_employee text)
RETURNS TABLE (level integer, employee_id text, approval_limit numeric)
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE chain AS (
        SELECT 0 AS level, e.employee_id, e.manager_id, e.approval_limit
        FROM ops.employee AS e
        WHERE e.employee_id = start_employee
        UNION ALL
        SELECT c.level + 1, m.employee_id, m.manager_id, m.approval_limit
        FROM chain AS c
        INNER JOIN ops.employee AS m ON m.employee_id = c.manager_id
        WHERE c.level < 10
    )
    SELECT chain.level, chain.employee_id, chain.approval_limit FROM chain ORDER BY chain.level;
$$;

-- Lowest person in the chain (buyer first) whose limit covers the amount; NULL
-- if nobody can.
CREATE OR REPLACE FUNCTION ops.required_approver(
    buyer text, amount numeric
) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT ac.employee_id FROM ops.approval_chain(buyer) AS ac
    WHERE ac.approval_limit >= amount
    ORDER BY ac.level
    LIMIT 1;
$$;
