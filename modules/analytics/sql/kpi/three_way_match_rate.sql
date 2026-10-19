-- kpi: three_way_match_rate
-- name: Three-way match rate
-- description: Share of invoices with no error-level exception (PO, receipt
-- and invoice agree within tolerance), by invoice month.
-- params: start_date:date, end_date:date
-- grain: month
SELECT
    date_trunc('month', m.invoice_date)::date AS month,
    count(*) AS invoices,
    count(*) FILTER (WHERE m.match_status = 'matched') AS matched,
    round(
        100.0 * count(*) FILTER (WHERE m.match_status = 'matched') / count(*), 2
    ) AS match_rate_pct
FROM ops.v_invoice_match_status AS m
WHERE m.invoice_date BETWEEN (:start_date)::date AND (:end_date)::date
GROUP BY 1
ORDER BY 1
