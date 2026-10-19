-- Singular test: mart spend must equal the operational ledger (non-rejected invoice totals).
select 1
from (
    select (select coalesce(sum(actual_total), 0) from {{ ref('mart_spend_monthly') }}) as mart,
           (select coalesce(sum(total), 0) from {{ source('ops', 'invoice') }} where status <> 'rejected') as ledger
) t
where abs(mart - ledger) > 0.01
