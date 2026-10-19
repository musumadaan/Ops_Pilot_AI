select
    s.supplier_id, s.supplier_name, s.country, s.payment_terms_days, s.supplier_status,
    count(f.invoice_id)                          as invoices,
    coalesce(sum(f.total), 0)                    as spend,
    round(100.0 * avg(case when f.is_clean_match then 1 else 0 end), 1) as clean_match_pct
from {{ ref('stg_supplier') }} s
left join {{ ref('fct_invoice') }} f using (supplier_id)
group by 1, 2, 3, 4, 5
