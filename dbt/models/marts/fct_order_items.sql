{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='delete+insert',
        on_schema_change='fail'
    )
}}
-- depends_on: {{ ref('stg_orders') }}
-- (el ref dentro de is_incremental() no se detecta solo; se declara aquí)
-- Hechos de ventas. Granularidad: 1 fila por línea de pedido (versión vigente del pedido).
-- Incremental por pedido: cuando llega una versión nueva, se borran TODAS sus líneas y se
-- insertan las nuevas (delete+insert por order_id), aunque cambie el número de líneas.

with items as (
    select * from {{ ref('stg_order_items') }}
    {% if is_incremental() %}
    where order_id in (
        select order_id from {{ ref('stg_orders') }}
        where _ingested_at > (select coalesce(max(_ingested_at), '1900-01-01') from {{ this }})
    )
    {% endif %}
)

select
    i.order_id,
    i.line_no,
    i.order_date,
    i.order_ts,
    i.customer_id,
    c.customer_sk,  -- versión del cliente vigente en el momento del pedido (point-in-time)
    i.product_id,
    i.status,
    i.status not in ('cancelled', 'returned') as is_revenue,
    i.currency,
    i.quantity,
    i.unit_price,
    i.line_amount,
    i._batch_id,
    i._ingested_at
from items as i
left join {{ ref('dim_customer') }} as c
    on c.customer_id = i.customer_id
    and i.order_ts >= c.valid_from
    and (c.valid_to is null or i.order_ts < c.valid_to)
