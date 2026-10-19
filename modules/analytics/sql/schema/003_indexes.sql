-- Indexes chosen from the access paths in the views and KPIs (see scripts/explain_demo.py for
-- before/after EXPLAIN ANALYZE evidence). Composite and partial indexes are used on purpose.

CREATE INDEX invoice_supplier_date_idx ON ops.invoice (supplier_id, invoice_date, invoice_id);
CREATE INDEX invoice_po_idx ON ops.invoice (po_number) WHERE po_number IS NOT NULL;
CREATE INDEX invoice_dup_key_idx ON ops.invoice
    (supplier_id, upper(regexp_replace(invoice_number, '[^A-Za-z0-9]', '', 'g')));
-- Open work queue: only unpaid, not-yet-decided invoices (small, hot subset).
CREATE INDEX invoice_open_idx ON ops.invoice (received_at)
    WHERE status IN ('received', 'extracted', 'exception');
CREATE INDEX invoice_line_po_idx ON ops.invoice_line (po_number, po_line_no)
    WHERE po_number IS NOT NULL;
CREATE INDEX receipt_line_po_idx ON ops.receipt_line (po_number, po_line_no);
CREATE INDEX goods_receipt_po_idx ON ops.goods_receipt (po_number, received_date);
CREATE INDEX purchase_order_supplier_idx ON ops.purchase_order (supplier_id, order_date);
CREATE INDEX payment_invoice_idx ON ops.payment (invoice_id);
CREATE INDEX audit_event_entity_idx ON ops.audit_event (entity, entity_id, event_time);
CREATE INDEX case_evidence_case_idx ON ops.case_evidence (case_id);
