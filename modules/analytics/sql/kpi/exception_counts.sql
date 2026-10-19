-- kpi: exception_counts
-- name: Exceptions by code
-- description: Number and share of invoices carrying each exception code in
-- the period, most frequent first.
-- params: start_date:date, end_date:date
-- grain: exception_code
WITH scope AS (
    SELECT i.invoice_id
    FROM ops.invoice AS i
    WHERE
        i.status <> 'rejected'
        AND i.invoice_date BETWEEN CAST(:start_date AS date) AND CAST(
            :end_date AS date
        )
)

SELECT
    e.exception_code,
    e.severity,
    COUNT(DISTINCT e.invoice_id) AS invoices,
    ROUND(
        100.0
        * COUNT(DISTINCT e.invoice_id)
        / NULLIF((SELECT COUNT(*) FROM scope), 0),
        2
    ) AS pct_of_invoices
FROM ops.v_invoice_exceptions AS e
INNER JOIN scope AS s ON e.invoice_id = s.invoice_id
GROUP BY e.exception_code, e.severity
ORDER BY invoices DESC, e.exception_code ASC
