select payment_id, invoice_id, paid_date, amount, method from {{ source('ops', 'payment') }}
