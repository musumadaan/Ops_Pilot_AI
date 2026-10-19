-- kpi: open_commitments
-- name: Open PO commitments
-- description: Value of ordered-but-not-yet-invoiced quantity on open purchase
-- orders, by category (at PO prices, before tax).
-- params:
-- grain: category
SELECT
    po.category,
    count(DISTINCT po.po_number) AS open_pos,
    round(sum(greatest(s.qty_open_to_invoice, 0) * s.unit_price), 2)
        AS open_commitment
FROM ops.v_po_line_status AS s
INNER JOIN ops.purchase_order AS po ON s.po_number = po.po_number
WHERE po.status = 'open'
GROUP BY po.category
ORDER BY open_commitment DESC, po.category ASC
