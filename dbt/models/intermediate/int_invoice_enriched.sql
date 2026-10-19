-- One row per invoice with supplier, PO category, exception counts and payment facts.
with exc as (
    select invoice_id,
           count(*) filter (where severity = 'error')   as n_error_exceptions,
           count(*) filter (where severity = 'warning') as n_warning_exceptions
    from {{ ref('stg_invoice_exceptions') }}
    group by invoice_id
),
pay as (
    select invoice_id, min(paid_date) as first_paid_date, sum(amount) as paid_amount
    from {{ ref('stg_payment') }}
    group by invoice_id
)
select
    i.*,
    s.supplier_name,
    coalesce(po.category, 'Uncategorized') as category,
    coalesce(exc.n_error_exceptions, 0)   as n_error_exceptions,
    coalesce(exc.n_warning_exceptions, 0) as n_warning_exceptions,
    pay.first_paid_date,
    coalesce(pay.paid_amount, 0) as paid_amount,
    case when pay.first_paid_date is not null then pay.first_paid_date <= i.due_date end as paid_on_time
from {{ ref('stg_invoice') }} i
join {{ ref('stg_supplier') }} s using (supplier_id)
left join {{ ref('stg_purchase_order') }} po using (po_number)
left join exc using (invoice_id)
left join pay using (invoice_id)
