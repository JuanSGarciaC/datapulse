-- SCD2: exactamente una versión vigente por cliente y sin solapes ni huecos entre versiones.
with ordered as (
    select
        customer_id,
        valid_from,
        valid_to,
        is_current,
        lead(valid_from) over (partition by customer_id order by version_number) as next_from
    from {{ ref('dim_customer') }}
)
select customer_id, 'varias o ninguna versión vigente' as problem
from {{ ref('dim_customer') }}
group by customer_id
having count(*) filter (where is_current) <> 1
union all
select customer_id, 'hueco o solape entre versiones'
from ordered
where next_from is not null and next_from <> valid_to
