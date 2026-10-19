select po_number, supplier_id, buyer_id, category, currency, order_date, status as po_status, cost_center
from {{ source('ops', 'purchase_order') }}
