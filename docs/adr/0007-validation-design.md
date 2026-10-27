# ADR-0007: Validación previa a la carga, cuarentena y presupuesto de errores

- **Estado:** aceptada (implementada en fase 3)
- **Fecha:** 2026-10-27

## Decisiones

1. **Todo entra como texto y se convierte con reglas explícitas.** CSV y API se leen como
   `String` y cada columna se tipa con su regla. Un `"31/02/2026"` o un `"two"` se detectan como
   error de tipo; ninguna librería los “arregla” en silencio.
2. **La herramienta según la forma del dato:**
   - Tabular (clientes CSV y productos aplanados): **Polars**, con reglas vectorizadas.
   - Documentos anidados (pedidos con líneas): **validadores Python explícitos**. Aplanar
     primero obligaría a inferir un tipo para `quantity` y el valor inválido se perdería.
3. **Errores por fila → cuarentena, no se detiene el lote.** La fila va a `raw.quarantine` con
   el payload original y la lista de reglas que falló. El resto del lote se carga.
4. **Presupuesto de errores (20 % por defecto).** Si falla más de esa proporción, el problema
   es del archivo y no de filas sueltas: el lote se **rechaza entero** (`status=rejected`).
5. **Cambios de esquema:**
   - Si falta una columna obligatoria, el lote se rechaza.
   - Si falta una columna opcional, se rellena con nulos y se emite un aviso.
   - Si llega una columna nueva, se acepta, se guarda en `_extra` (no se pierde) y se emite un aviso.
6. **Las referencias huérfanas no se ponen en cuarentena** (un pedido de un producto
   desconocido). Antes de cargar no se sabe si el producto llegará después; se comprueba
   después de transformar, con una prueba de relaciones de dbt con severidad `warn`.
7. **Reglas con nombre estable** (`not_null:email`, `type:items.quantity`, `range:price`…).
   Cada regla evaluada en cada lote queda en `ops.quality_results` con filas evaluadas, filas
   fallidas y hasta 5 claves de ejemplo. Es lo que muestra el dashboard.

## Verificación
`tests/integration/test_ingestion.py::test_pipeline_detects_exactly_the_manifest_defects`
compara cada regla con los defectos que el generador declaró en `manifest.json`. Si una regla
detecta de menos o de más, la prueba falla.

## Alternativas descartadas
| Opción | Motivo |
|--------|--------|
| Great Expectations / Pandera | Aportan mucho para catálogos grandes de reglas; aquí añadirían una dependencia pesada y otra capa de configuración para ~30 reglas. Las reglas propias son explícitas y fáciles de leer. |
| Rechazar el lote ante cualquier error | Un correo mal escrito bloquearía 200 clientes válidos. |
| Cargar todo y validar solo en dbt | La capa `raw` tendría basura con tipos de texto y se perdería la distinción entre “dato inválido” y “dato válido pero inconsistente”. |
