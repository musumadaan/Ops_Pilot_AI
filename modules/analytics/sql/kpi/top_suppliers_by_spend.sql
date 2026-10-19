-- kpi: top_suppliers_by_spend
-- name: Top suppliers by spend
-- description: Spend (invoice totals), share and cumulative share per
-- supplier, ranked. Windows are computed over all suppliers before the top-N
-- limit.
-- params: start_date:date, end_date:date, top_n:int
-- grain: supplier
-- engines: postgres, duckdb
WITH s AS (
    SELECT
        i.supplier_id,
        sup.name,
        sum(i.total) AS spend,
        count(*) AS invoices
    FROM ops.invoice AS i
    INNER JOIN ops.supplier AS sup ON i.supplier_id = sup.supplier_id
    WHERE
        i.status <> 'rejected'
        AND i.invoice_date BETWEEN cast(:start_date AS date) AND cast(
            :end_date AS date
        )
    GROUP BY i.supplier_id, sup.name
)

SELECT
    supplier_id,
    name,
    spend,
    invoices,
    rank() OVER (ORDER BY spend DESC, supplier_id ASC) AS spend_rank,
    round(100.0 * spend / sum(spend) OVER (), 2) AS share_pct,
    round(
        100.0
        * sum(spend) OVER (ORDER BY spend DESC, supplier_id ASC)
        / sum(spend) OVER (),
        2
    ) AS cumulative_share_pct
FROM s
ORDER BY spend_rank
LIMIT cast(:top_n AS int)
