-- Dimensión de productos SCD tipo 1 (estado actual). Granularidad: 1 fila por producto.
select
    product_id,
    title,
    category,
    brand,
    sku,
    price as current_price,
    discount_percentage,
    rating,
    stock,
    availability_status,
    created_at,
    updated_at,
    _batch_id
from {{ ref('stg_products') }}
