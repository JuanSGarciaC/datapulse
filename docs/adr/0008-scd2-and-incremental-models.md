# ADR-0008: SCD2 desde la historia de raw e incremental por pedido

- **Estado:** aceptada (implementada en fase 5)
- **Fecha:** 2026-10-27

## Contexto
Los atributos de los clientes cambian (ciudad, segmento) y los pedidos reciben versiones
tardías (cancelaciones, devoluciones). Los indicadores deben ser correctos *en el momento del
pedido* y reconstruibles.

## Decisiones
1. **`dim_customer` (SCD2) es un modelo normal**, calculado con funciones de ventana sobre
   `stg_customer_versions`, en lugar de un `dbt snapshot`.
2. **`fct_order_items` es incremental** con `incremental_strategy='delete+insert'` y
   `unique_key='order_id'`: borra todas las líneas del pedido y vuelve a insertar las de la
   versión vigente.
3. **El cliente se une *point-in-time***: la versión cuyo intervalo
   `[valid_from, valid_to)` contiene `order_ts`.

## Alternativas descartadas
| Opción | Motivo |
|--------|--------|
| `dbt snapshot` | Solo captura el estado cuando se ejecuta; si se pierde una ejecución, la historia no vuelve. No se puede reconstruir con `--full-refresh`. |
| Incremental `merge` por `(order_id, line_no)` | Si una versión nueva trae menos líneas, las sobrantes quedan como fantasmas. |
| Tabla completa en cada ejecución | Correcto pero no demuestra estrategia incremental; con más volumen sería caro. |
| Unir siempre la versión actual del cliente | Cambiaría retroactivamente el segmento de ventas pasadas. |

## Consecuencias
- Un `--full-refresh` produce exactamente el mismo resultado que la carga incremental día a
  día. Está probado fila a fila.
- `raw` debe conservar toda la historia, cosa que ya garantiza el diseño append-only (ADR-0003).
- **Limitación:** si una corrección *anterior* de un cliente llega tarde (un `updated_at` más
  antiguo que otro ya cargado), `dim_customer` se corrige sola en la siguiente ejecución, pero
  los hechos ya calculados de forma incremental no se reasignan hasta un `--full-refresh`. Una
  ejecución completa semanal programada lo resolvería; queda documentado.
