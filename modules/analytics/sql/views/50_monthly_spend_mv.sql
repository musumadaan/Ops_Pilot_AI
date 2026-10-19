-- Pre-aggregated monthly spend for dashboards. Refresh after each load:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY ops.mv_monthly_spend;
CREATE MATERIALIZED VIEW ops.mv_monthly_spend AS
SELECT
    i.supplier_id,
    date_trunc('month', i.invoice_date)::date AS month,
    coalesce(po.category, 'Uncategorized') AS category,
    count(*) AS invoice_count,
    sum(i.subtotal) AS subtotal,
    sum(i.total) AS total
FROM ops.invoice AS i
LEFT JOIN ops.purchase_order AS po ON i.po_number = po.po_number
WHERE i.status <> 'rejected'
GROUP BY
    date_trunc('month', i.invoice_date)::date,
    i.supplier_id,
    coalesce(po.category, 'Uncategorized');

CREATE UNIQUE INDEX mv_monthly_spend_uidx ON ops.mv_monthly_spend (
    month, supplier_id, category
);
