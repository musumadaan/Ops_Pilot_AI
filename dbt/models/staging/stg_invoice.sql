select
    invoice_id,
    supplier_id,
    invoice_number,
    po_number,
    invoice_date,
    received_at,
    approved_at,
    due_date,
    currency,
    subtotal,
    tax,
    total,
    status,
    source,
    (approved_at is not null) as is_approved,
    extract(epoch from approved_at - received_at) / 86400.0 as cycle_days
from {{ source('ops', 'invoice') }}
