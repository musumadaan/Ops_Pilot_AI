select invoice_id from {{ ref('stg_invoice') }} where abs(total - (subtotal + tax)) > 0.01
