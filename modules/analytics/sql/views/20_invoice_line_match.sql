-- Line-level three-way match: invoice line vs PO line vs goods received (all
-- receipts to date).
-- * running invoiced quantity uses a window function (order: invoice date,
-- then id)
-- * receipts and the applicable contract tolerance use LATERAL joins
CREATE VIEW ops.v_invoice_line_match AS
WITH params AS (
    SELECT
        max(value) FILTER (WHERE key = 'default_price_tolerance_pct')
            AS default_tol
    FROM ops.control_parameter
),

lines AS (
    SELECT
        il.invoice_id,
        il.line_no,
        inv.supplier_id,
        inv.invoice_date,
        il.po_number,
        il.po_line_no,
        il.qty,
        il.unit_price,
        il.amount,
        sum(il.qty) OVER (
            PARTITION BY il.po_number, il.po_line_no
            ORDER BY inv.invoice_date, il.invoice_id, il.line_no
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS qty_invoiced_cum
    FROM ops.invoice_line AS il
    INNER JOIN ops.invoice AS inv ON il.invoice_id = inv.invoice_id
    WHERE inv.status <> 'rejected'
)

SELECT
    l.invoice_id,
    l.line_no,
    l.supplier_id,
    l.invoice_date,
    l.po_number,
    l.po_line_no,
    l.qty,
    l.unit_price,
    l.amount,
    l.qty_invoiced_cum,
    pl.qty_ordered,
    pl.unit_price AS po_unit_price,
    coalesce(r.qty_received, 0) AS qty_received,
    CASE
        WHEN
            pl.unit_price > 0
            THEN round((l.unit_price - pl.unit_price) / pl.unit_price * 100, 2)
    END
        AS price_variance_pct,
    coalesce(c.price_tolerance_pct, p.default_tol) AS price_tolerance_pct
FROM lines AS l
CROSS JOIN params AS p
LEFT JOIN
    ops.po_line AS pl
    ON l.po_number = pl.po_number AND l.po_line_no = pl.line_no
LEFT JOIN ops.purchase_order AS po ON l.po_number = po.po_number
LEFT JOIN LATERAL (
    SELECT sum(rl.qty_received) AS qty_received
    FROM ops.receipt_line AS rl
    WHERE rl.po_number = l.po_number AND rl.po_line_no = l.po_line_no
) AS r ON true
LEFT JOIN LATERAL (
    SELECT ct.price_tolerance_pct
    FROM ops.contract AS ct
    WHERE
        ct.supplier_id = l.supplier_id
        AND ct.category = po.category
        AND l.invoice_date BETWEEN ct.effective_from AND coalesce(
            ct.effective_to, DATE '9999-12-31'
        )
    ORDER BY ct.effective_from DESC
    LIMIT 1
) AS c ON true;
