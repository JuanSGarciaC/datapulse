-- Granularidad: 1 fila por línea de pedido (de la versión vigente del pedido).
select
    o.order_id,
    (item ->> 'line_no')::int as line_no,
    (item ->> 'product_id')::int as product_id,
    (item ->> 'quantity')::int as quantity,
    (item ->> 'unit_price')::numeric(12, 2) as unit_price,
    ((item ->> 'quantity')::int * (item ->> 'unit_price')::numeric(12, 2))::numeric(14, 2) as line_amount,
    o.customer_id,
    o.order_ts,
    o.order_date,
    o.status,
    o.currency,
    o._batch_id,
    o._ingested_at
from {{ ref('stg_orders') }} as o
cross join lateral jsonb_array_elements(o.items) as item
