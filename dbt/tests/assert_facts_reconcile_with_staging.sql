-- Conciliación de volumen: cada línea vigente de staging está en los hechos y viceversa.
(select order_id, line_no from {{ ref('stg_order_items') }}
 except
 select order_id, line_no from {{ ref('fct_order_items') }})
union all
(select order_id, line_no from {{ ref('fct_order_items') }}
 except
 select order_id, line_no from {{ ref('stg_order_items') }})
