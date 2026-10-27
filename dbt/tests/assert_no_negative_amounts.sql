select order_id, line_no, line_amount
from {{ ref('fct_order_items') }}
where line_amount < 0 or quantity <= 0
