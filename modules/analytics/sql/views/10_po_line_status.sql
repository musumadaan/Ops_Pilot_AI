-- Ordered / received / invoiced quantities per PO line (rejected invoices
-- excluded).
CREATE VIEW ops.v_po_line_status AS
WITH r AS (
    SELECT
        po_number,
        po_line_no,
        sum(qty_received) AS qty_received
    FROM ops.receipt_line
    GROUP BY po_number, po_line_no
),

i AS (
    SELECT
        il.po_number,
        il.po_line_no,
        sum(il.qty) AS qty_invoiced
    FROM ops.invoice_line AS il
    INNER JOIN ops.invoice AS inv ON il.invoice_id = inv.invoice_id
    WHERE inv.status <> 'rejected'
    GROUP BY il.po_number, il.po_line_no
)

SELECT
    pl.po_number,
    pl.line_no,
    pl.qty_ordered,
    pl.unit_price,
    coalesce(r.qty_received, 0) AS qty_received,
    coalesce(i.qty_invoiced, 0) AS qty_invoiced,
    pl.qty_ordered - coalesce(i.qty_invoiced, 0) AS qty_open_to_invoice,
    round(pl.qty_ordered * pl.unit_price, 2) AS ordered_value
FROM ops.po_line AS pl
LEFT JOIN r ON pl.po_number = r.po_number AND pl.line_no = r.po_line_no
LEFT JOIN i ON pl.po_number = i.po_number AND pl.line_no = i.po_line_no;
