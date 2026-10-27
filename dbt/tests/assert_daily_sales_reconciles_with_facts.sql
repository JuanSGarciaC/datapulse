-- Conciliación: el ingreso agregado debe coincidir exactamente con la suma de las líneas.
with facts as (
    select coalesce(sum(line_amount), 0) as total from {{ ref('fct_order_items') }} where is_revenue
),
daily as (
    select coalesce(sum(revenue), 0) as total from {{ ref('fct_daily_sales') }}
)
select facts.total as facts_total, daily.total as daily_total
from facts, daily
where facts.total <> daily.total
