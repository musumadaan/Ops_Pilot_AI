select
    date_trunc('month', f.invoice_date)::date as month,
    f.category,
    count(*)        as invoices,
    sum(f.subtotal) as actual_subtotal,
    sum(f.total)    as actual_total,
    b.amount        as budget,
    round(100.0 * (sum(f.subtotal) - b.amount) / nullif(b.amount, 0), 2) as variance_pct
from {{ ref('fct_invoice') }} f
left join {{ source('ops', 'budget') }} b
       on b.category = f.category and b.period = date_trunc('month', f.invoice_date)::date
group by 1, 2, b.amount
