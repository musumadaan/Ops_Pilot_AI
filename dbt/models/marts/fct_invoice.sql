select
    invoice_id, supplier_id, supplier_name, category, invoice_number, po_number,
    invoice_date, due_date, subtotal, tax, total, status,
    cycle_days, n_error_exceptions, n_warning_exceptions,
    (n_error_exceptions = 0) as is_clean_match,
    first_paid_date, paid_amount, paid_on_time
from {{ ref('int_invoice_enriched') }}
where status <> 'rejected'
