-- Dimensión de clientes SCD tipo 2. Granularidad: 1 fila por versión de cliente.
-- Se construye desde la historia completa de raw (no con snapshots de dbt): así se
-- puede reconstruir en cualquier momento y el resultado es determinista (ADR-0008).
with versions as (
    select
        *,
        lead(updated_at) over (partition by customer_id order by updated_at) as next_updated_at,
        row_number() over (partition by customer_id order by updated_at) as version_number
    from {{ ref('stg_customer_versions') }}
)

select
    md5(customer_id || '|' || updated_at::text) as customer_sk,
    customer_id,
    version_number,
    first_name,
    last_name,
    email_sha256,
    city,
    country,
    segment,
    signup_date,
    loyalty_tier,
    -- La primera versión cubre también el pasado: un pedido anterior a la primera
    -- exportación del cliente debe encontrar su versión.
    case when version_number = 1 then '1900-01-01'::timestamptz else updated_at end as valid_from,
    next_updated_at as valid_to,
    next_updated_at is null as is_current,
    _batch_id
from versions
