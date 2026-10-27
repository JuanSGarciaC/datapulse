-- Granularidad: 1 fila por versión distinta de un cliente (base del SCD2).
-- Dos versiones con el mismo updated_at se resuelven por orden de llegada.
with ranked as (
    select
        *,
        row_number() over (
            partition by customer_id, updated_at
            order by _ingested_at desc, _raw_id desc
        ) as dup_rank
    from {{ source('raw', 'customers') }}
)

select
    customer_id,
    first_name,
    last_name,
    encode(sha256(convert_to(lower(email), 'UTF8')), 'hex') as email_sha256,
    city,
    country,
    segment,
    signup_date,
    _extra ->> 'loyalty_tier' as loyalty_tier,
    updated_at,
    _batch_id
from ranked
where dup_rank = 1
