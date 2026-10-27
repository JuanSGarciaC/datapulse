# ADR-0004: Estrategia de idempotencia y carga incremental

- **Estado:** aceptada (fase 1: estructura en `ops`; fases 3–5: implementación)
- **Fecha:** 2026-10-27

## Objetivo
Ejecutar el mismo pipeline N veces con la misma entrada produce **exactamente** el mismo estado.

## Mecanismos
| Nivel | Mecanismo | Implementado en |
|-------|-----------|-----------------|
| Archivo | sha256 del contenido; único `(source_name, content_sha256)` en `ops.ingestion_registry`. Un archivo renombrado pero idéntico no se recarga | Fase 1 (constraint) / fase 3 (lógica) |
| API | Marca de agua `updated_at` en `ops.watermarks` con **ventana de solapamiento** para registros tardíos | Fase 3–4 |
| Registro | `_record_hash` + índice único en `raw` con `ON CONFLICT DO NOTHING` (absorbe el solapamiento) | Fase 3 |
| Modelo | dbt incremental con `unique_key` (`delete+insert`) | Fase 5 |

## Recuperación
- Un lote en estado `failed` se puede reintentar: se actualiza la misma fila del registro.
- La marca de agua **solo avanza** en la misma transacción que confirma la carga del lote.
  Si la carga falla, la marca de agua no se mueve y el siguiente intento repite la ventana.

## Por qué hash de contenido y no nombre de archivo
Los nombres mienten (reenvíos con otro nombre, mismo nombre con contenido corregido). El hash
identifica el contenido real.
