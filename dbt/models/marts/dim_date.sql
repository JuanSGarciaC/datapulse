-- Dimensión de fechas. Granularidad: 1 fila por día, cubre el rango de pedidos ± 30 días.
with bounds as (
    select
        coalesce(min(order_date), current_date) - 30 as first_day,
        coalesce(max(order_date), current_date) + 30 as last_day
    from {{ ref('stg_orders') }}
)

select
    day::date as date_day,
    extract(year from day)::int as year,
    extract(quarter from day)::int as quarter,
    extract(month from day)::int as month,
    to_char(day, 'YYYY-MM') as year_month,
    extract(isodow from day)::int as iso_weekday,
    extract(isodow from day) in (6, 7) as is_weekend
from bounds, generate_series(bounds.first_day, bounds.last_day, interval '1 day') as day
