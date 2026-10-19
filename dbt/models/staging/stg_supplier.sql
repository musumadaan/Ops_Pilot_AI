select supplier_id, name as supplier_name, country, payment_terms_days, status as supplier_status, created_on
from {{ source('ops', 'supplier') }}
