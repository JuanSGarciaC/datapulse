-- Granularidad: 1 fila por pedido (su versión más reciente: gana el updated_at mayor).
with ranked as (
    select
        *,
        row_number() over (
            partition by order_id
            order by updated_at desc, _ingested_at desc, _raw_id desc
        ) as version_rank,
        count(*) over (partition by order_id) as versions_received
    from {{ source('raw', 'orders') }}
)

select
    order_id,
    customer_id,
    order_ts,
    (order_ts at time zone 'UTC')::date as order_date,
    status,
    currency,
    updated_at,
    items,
    versions_received,
    _batch_id,
    _ingested_at
from ranked
where version_rank = 1
