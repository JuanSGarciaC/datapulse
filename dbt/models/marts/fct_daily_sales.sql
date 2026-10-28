{{ config(indexes=[{'columns': ['order_date']}]) }}
-- Agregado diario. Granularidad: 1 fila por (día, categoría). Solo ventas efectivas
-- (sin cancelados ni devueltos) en revenue/units; las anulaciones se cuentan aparte.
select
    f.order_date,
    coalesce(p.category, 'desconocida') as category,
    count(distinct f.order_id) filter (where f.is_revenue) as orders,
    coalesce(sum(f.quantity) filter (where f.is_revenue), 0) as units,
    coalesce(sum(f.line_amount) filter (where f.is_revenue), 0)::numeric(14, 2) as revenue,
    count(distinct f.order_id) filter (where not f.is_revenue) as cancelled_or_returned_orders
from {{ ref('fct_order_items') }} as f
left join {{ ref('dim_product') }} as p on p.product_id = f.product_id
group by 1, 2
