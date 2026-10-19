-- kpi: approval_load
-- name: Approval load by required approver
-- description: Finds the first approver in the buyer's management chain
-- whose limit covers the invoice total; NULL means no approver qualifies.
-- params: start_date:date, end_date:date
-- grain: approver
SELECT
    ops.required_approver(po.buyer_id, i.total) AS approver_id,
    count(*) AS invoices,
    round(sum(i.total), 2) AS total_value
FROM ops.invoice AS i
INNER JOIN ops.purchase_order AS po ON i.po_number = po.po_number
WHERE
    i.status <> 'rejected'
    AND i.invoice_date BETWEEN cast(:start_date AS date) AND cast(
        :end_date AS date
    )
GROUP BY approver_id
ORDER BY invoices DESC, approver_id ASC
