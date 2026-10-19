-- kpi: spend_variance_pct
-- name: Spend variance %
-- description: (actual subtotal - budget) / budget x 100 by category and
-- month. Actuals are non-rejected invoices (before tax).
-- params: start_date:date, end_date:date
-- grain: category, month
-- engines: postgres, duckdb
WITH actual AS (
    SELECT
        coalesce(po.category, 'Uncategorized') AS category,
        date_trunc('month', i.invoice_date)::date AS month,
        sum(i.subtotal) AS actual
    FROM ops.invoice AS i
    LEFT JOIN ops.purchase_order AS po ON i.po_number = po.po_number
    WHERE
        i.status <> 'rejected'
        AND i.invoice_date BETWEEN (:start_date)::date AND (:end_date)::date
    GROUP BY category, month
)

SELECT
    a.category,
    a.month,
    a.actual,
    b.amount AS budget,
    round((a.actual - b.amount) / nullif(b.amount, 0) * 100, 2) AS variance_pct
FROM actual AS a
LEFT JOIN ops.budget AS b ON a.category = b.category AND a.month = b.period
ORDER BY a.month, a.category
