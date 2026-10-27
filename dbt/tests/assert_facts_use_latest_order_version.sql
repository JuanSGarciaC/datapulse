-- Los hechos reflejan la última versión de cada pedido (actualizaciones tardías incluidas).
select f.order_id
from {{ ref('fct_order_items') }} as f
join {{ ref('stg_orders') }} as o using (order_id)
where f.status <> o.status
