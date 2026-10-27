-- Granularidad: 1 fila por producto (versión más reciente).
with ranked as (
    select
        *,
        row_number() over (
            partition by product_id
            order by updated_at desc, _ingested_at desc, _raw_id desc
        ) as version_rank
    from {{ source('raw', 'products') }}
)

select
    product_id,
    title,
    category,
    brand,
    sku,
    price,
    discount_percentage,
    rating,
    stock,
    availability_status,
    created_at,
    updated_at,
    _batch_id
from ranked
where version_rank = 1
