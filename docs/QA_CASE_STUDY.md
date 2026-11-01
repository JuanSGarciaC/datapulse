# Caso de estudio QA: un pedido con cantidad negativa

Este documento sigue un defecto de datos real del conjunto de ejemplo desde el archivo de origen
hasta el indicador. El objetivo es mostrar, con evidencia consultable, qué regla lo detecta, dónde
queda guardado, qué habría pasado sin la regla y qué prueba confirma que los números cuadran.

Todas las cifras salen de una ejecución local del pipeline (`docker compose` + `python -m
datapulse.flows run daily` con `data/samples`) del 2026-11-01. Las consultas SQL están al final
para repetirlas.

## 1. El defecto

`data/samples/orders/orders_batch_002.json`, fila 116. El generador lo declara en
`data/samples/manifest.json` como `out_of_range` en `items.quantity` (uno de los 37 defectos
sembrados a propósito).

```json
{
  "order_id": "O000266", "customer_id": "C00167", "status": "placed", "currency": "USD",
  "order_ts": "2026-01-02T01:09:00Z", "updated_at": "2026-01-02T01:33:00Z",
  "items": [
    {"line_no": 1, "product_id": 81,  "quantity": -1, "unit_price": 1687.37},
    {"line_no": 2, "product_id": 124, "quantity": 5,  "unit_price": 410.39}
  ]
}
```

Una cantidad de -1 en un pedido en estado `placed` no es una devolución (las devoluciones llegan
como cambio de `status`); es un dato inválido de la fuente.

## 2. La regla que lo detecta

Contrato de pedidos, [`src/datapulse/validation/orders.py`](../src/datapulse/validation/orders.py):

```python
elif quantity <= 0:
    add("range:items.quantity", "items.quantity", f"debe ser > 0, llegó {quantity}")
```

La validación ocurre **antes de cargar** (`pre_load`) y se aplica por pedido: una línea inválida
manda a cuarentena el pedido completo, porque cargar solo la línea 2 dejaría un pedido a medias
con un total que no existe en la fuente.

Resultado registrado en `ops.quality_results` para ese lote:

| Regla | Severidad | Filas evaluadas | Filas fallidas | Muestra |
|---|---|---|---|---|
| `range:items.quantity` | error | 155 | 2 | `O000231` (cantidad 0), `O000266` (cantidad -1) |
| `not_null:customer_id` | error | 155 | 2 | `O000170`, `O000211` |
| Las otras 19 reglas del contrato | error/warn | 155 | 0 | — |

## 3. Cuarentena con motivo

El pedido no se pierde ni se corrige en silencio: queda en `raw.quarantine` con el documento
original, su hash y el motivo.

| Campo | Valor |
|---|---|
| `source_name` / `_source_ref` / `_row_number` | `orders_json` / `orders_batch_002.json` / 116 |
| `record_key` | `O000266` |
| `errors` | `[{"rule": "range:items.quantity", "field": "items.quantity", "message": "debe ser > 0, llegó -1"}]` |

## 4. Decisión sobre el lote: presupuesto de errores

El lote tenía 155 pedidos y 4 fueron a cuarentena (2,6 %). El presupuesto por lote es del 20 %
(`max_error_rate=0.2` en `validate_orders`): por debajo, el problema es de filas sueltas y el lote
se carga; por encima, el problema es del archivo y se rechaza entero
(`ValidationReport.enforce_error_budget`).

`ops.ingestion_registry` para el lote: `status = success`, `row_count = 155`,
`rejected_count = 4`.

## 5. Impacto: qué habría mostrado el indicador sin la regla

| | Valor |
|---|---|
| Ingreso de la línea 1 si se hubiera cargado | -1 × 1687,37 = **-1687,37 USD** (categoría `smartphones`) |
| Ingreso real publicado de `smartphones` el 2026-01-02 | 38 219,56 USD |
| Ingreso de `smartphones` con la línea inválida | 36 532,19 USD (-4,4 %) |

Además, las unidades del día bajarían y el pedido contaría como venta aunque su total no sea fiable.
La regla evita que el error llegue a `marts.fct_daily_sales`, que es lo que lee la API y el
dashboard.

Lo que la regla **no** hace: no recupera la línea 2 (5 × 410,39 = 2051,95 USD de `tablets`).
Ese importe falta del indicador hasta que la fuente reenvíe el pedido corregido. Es una decisión
consciente: preferimos un indicador incompleto y explicado a uno con un total inventado.

## 6. Conciliación: las pruebas que confirman que los números cuadran

Después de dbt, estas pruebas singulares (`dbt/tests/`) pasaron en la misma ejecución:

| Prueba | Qué comprueba |
|---|---|
| `assert_no_negative_amounts` | Ninguna línea de `fct_order_items` tiene `quantity <= 0` ni importe negativo (segunda barrera si la primera fallara). |
| `assert_facts_reconcile_with_staging` | Cada línea vigente de staging está en los hechos y viceversa (no se pierden ni se inventan líneas). |
| `assert_daily_sales_reconciles_with_facts` | El agregado diario suma exactamente lo mismo que los hechos. |

Y una prueba de integración exige que el pipeline detecte **exactamente** los defectos declarados
en el manifiesto, ni más ni menos
([`tests/integration/test_ingestion.py`](../tests/integration/test_ingestion.py)):

```python
assert failures["raw.orders|range:items.quantity"] == defects["orders_json", "out_of_range"]
```

Las pruebas unitarias cubren los valores límite `0` y `-1`
([`tests/unit/test_validation_orders.py`](../tests/unit/test_validation_orders.py)).

## 7. Resultado

| Pregunta de QA | Respuesta con evidencia |
|---|---|
| ¿Se detectó? | Sí, antes de cargar, regla `range:items.quantity`. |
| ¿Se puede auditar? | Sí: documento original, fila, hash y motivo en `raw.quarantine`. |
| ¿Afectó al indicador? | No: `O000266` no está en `marts.fct_order_items`. |
| ¿Bloqueó el lote? | No: 2,6 % de error, por debajo del presupuesto del 20 %. |
| ¿Cuadran los totales? | Sí: las tres conciliaciones de dbt pasaron. |
| Limitación | La línea válida del mismo pedido tampoco se publica hasta que llegue corregido. |

## Repetirlo

Con el stack levantado y el pipeline ejecutado (ver [Inicio rápido](../README.md#inicio-rápido)):

```sql
-- docker compose exec postgres psql -U datapulse_admin -d datapulse
select record_key, errors from raw.quarantine where record_key = 'O000266';
select rule_name, rows_evaluated, rows_failed, details
  from ops.quality_results where rule_name = 'range:items.quantity';
select status, row_count, rejected_count
  from ops.ingestion_registry where source_ref = 'orders_batch_002.json';
select count(*) from marts.fct_order_items where order_id = 'O000266';  -- 0
select * from marts.fct_daily_sales where order_date = '2026-01-02' and category = 'smartphones';
```
