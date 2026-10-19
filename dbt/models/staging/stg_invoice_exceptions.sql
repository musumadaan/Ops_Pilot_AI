select invoice_id, supplier_id, exception_code, severity, occurrences, detail
from {{ source('ops', 'v_invoice_exceptions') }}
