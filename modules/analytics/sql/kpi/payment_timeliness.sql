-- kpi: payment_timeliness
-- name: Payment timeliness
-- description: For invoices paid in the period, by month of the due date:
-- share paid on or before the due date and average days late (late invoices
-- only).
-- params: start_date:date, end_date:date
-- grain: month
-- engines: postgres, duckdb
WITH paid AS (
    SELECT
        i.due_date,
        min(p.paid_date) AS paid_date
    FROM ops.invoice AS i
    INNER JOIN ops.payment AS p ON i.invoice_id = p.invoice_id
    WHERE
        i.status <> 'rejected'
        AND i.due_date BETWEEN cast(:start_date AS date) AND cast(
            :end_date AS date
        )
    GROUP BY i.invoice_id, i.due_date
)

SELECT
    cast(date_trunc('month', due_date) AS date) AS month,
    count(*) AS paid_invoices,
    round(100.0 * count(*) FILTER (WHERE paid_date <= due_date) / count(*), 2)
        AS on_time_pct,
    round(avg(paid_date - due_date) FILTER (WHERE paid_date > due_date), 2)
        AS avg_days_late
FROM paid
GROUP BY month
ORDER BY month
