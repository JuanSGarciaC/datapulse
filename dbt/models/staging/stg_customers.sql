-- Granularidad: 1 fila por cliente (su versión más reciente).
-- Desempate determinista: updated_at, luego orden de llegada.
with ranked as (
    select
        *,
        row_number() over (
            partition by customer_id
            order by updated_at desc, _ingested_at desc, _raw_id desc
        ) as version_rank
    from {{ source('raw', 'customers') }}
)

select
    customer_id,
    first_name,
    last_name,
    -- Minimización: los modelos expuestos usan el hash, nunca el correo en claro.
    encode(sha256(convert_to(lower(email), 'UTF8')), 'hex') as email_sha256,
    city,
    country,
    segment,
    signup_date,
    _extra ->> 'loyalty_tier' as loyalty_tier,
    updated_at,
    _batch_id,
    _ingested_at
from ranked
where version_rank = 1
