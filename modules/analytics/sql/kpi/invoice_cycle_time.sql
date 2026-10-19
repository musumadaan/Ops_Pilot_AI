-- kpi: invoice_cycle_time
-- name: Invoice cycle time (days, received to approved)
-- description: Mean, median (p50) and 90th percentile by month of receipt. Use
-- percentiles, not only the mean (long tail). Only approved invoices are
-- included.
-- params: start_date:date, end_date:date
-- grain: month
-- engines: postgres, duckdb
SELECT
    date_trunc('month', t.received_at AT TIME ZONE 'America/Edmonton')::date
        AS month,
    count(*) AS n,
    round(avg(t.days)::numeric, 2) AS mean_days,
    round(percentile_cont(0.5) WITHIN GROUP (ORDER BY t.days)::numeric, 2)
        AS p50_days,
    round(percentile_cont(0.9) WITHIN GROUP (ORDER BY t.days)::numeric, 2)
        AS p90_days
FROM (
    SELECT
        i.received_at,
        extract(EPOCH FROM i.approved_at - i.received_at) / 86400.0 AS days
    FROM ops.invoice AS i
    WHERE
        i.approved_at IS NOT NULL
        AND i.status <> 'rejected'
        AND i.invoice_date BETWEEN (:start_date)::date AND (:end_date)::date
) AS t
GROUP BY 1
ORDER BY 1
