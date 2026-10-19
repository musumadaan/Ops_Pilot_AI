-- kpi: supplier_concentration
-- name: Supplier concentration (HHI)
-- description: Herfindahl-Hirschman index of supplier spend shares (0-10000)
-- plus top-1 and top-5 share. Higher means more dependence on few suppliers.
-- params: start_date:date, end_date:date
-- grain: portfolio
-- engines: postgres, duckdb
WITH s AS (
    SELECT
        i.supplier_id,
        sum(i.total) AS spend
    FROM ops.invoice AS i
    WHERE
        i.status <> 'rejected'
        AND i.invoice_date BETWEEN cast(:start_date AS date) AND cast(
            :end_date AS date
        )
    GROUP BY i.supplier_id
),

ranked AS (
    SELECT
        spend,
        spend / sum(spend) OVER () AS share,
        row_number() OVER (ORDER BY spend DESC, supplier_id ASC) AS rn
    FROM s
)

SELECT
    count(*) AS suppliers,
    round(sum(share * share) * 10000, 1) AS hhi,
    round(100 * max(share), 2) AS top1_share_pct,
    round(100 * sum(share) FILTER (WHERE rn <= 5), 2) AS top5_share_pct
FROM ranked
