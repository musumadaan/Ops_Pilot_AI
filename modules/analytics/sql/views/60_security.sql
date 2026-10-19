-- Governance: views must respect row-level security of the *caller*, not the
-- view owner.
-- Without security_invoker a view runs with its owner's rights and silently
-- bypasses RLS.
ALTER VIEW ops.v_po_line_status SET (security_invoker = true);
ALTER VIEW ops.v_invoice_line_match SET (security_invoker = true);
ALTER VIEW ops.v_invoice_exceptions SET (security_invoker = true);
ALTER VIEW ops.v_invoice_match_status SET (security_invoker = true);

GRANT USAGE ON SCHEMA ops TO ops_analyst, ops_finance_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO ops_finance_admin;
GRANT SELECT ON ops.v_po_line_status,
ops.v_invoice_line_match,
ops.v_invoice_exceptions,
ops.v_invoice_match_status,
ops.invoice,
ops.invoice_line,
ops.supplier,
ops.purchase_order,
ops.po_line,
ops.goods_receipt,
ops.receipt_line,
ops.contract,
ops.control_parameter,
ops.exception_code, ops.user_supplier_access TO ops_analyst;
