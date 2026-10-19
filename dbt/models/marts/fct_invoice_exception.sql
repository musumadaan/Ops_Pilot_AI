select e.*, i.invoice_date, i.total as invoice_total
from {{ ref('stg_invoice_exceptions') }} e
join {{ ref('stg_invoice') }} i using (invoice_id)
