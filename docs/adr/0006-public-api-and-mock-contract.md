# ADR-0006: DummyJSON como API real + réplica simulada validada por contrato

- **Estado:** aceptada (implementada en fase 2)
- **Fecha:** 2026-10-27

## Contexto
El pipeline debe ingerir una API REST paginada y demostrar reintentos, límite de peticiones,
recuperación y carga incremental. Una API pública aporta realismo, pero no permite provocar
fallos a voluntad ni es determinista. Una simulación pura permite todo eso, pero puede
alejarse de cómo se comporta una API real.

## Decisión
1. **DummyJSON** como fuente real: gratuita, sin registro, dominio e-commerce, paginación
   `limit`/`skip`, `delay` y `/http/{status}`. Su comportamiento se verificó con peticiones
   en vivo (ver `docs/sources.md`).
2. **`mock-source`** replica ese contrato y añade lo que falta: fallos intermitentes por
   offset, `fail_rate` con semilla, 429 con `Retry-After` y un reloj de catálogo.
3. **Una sola suite de contrato** se ejecuta contra ambas. La simulación no puede alejarse de
   la realidad sin que CI lo detecte (cada push y cada semana).

## Alternativas descartadas
| Opción | Motivo |
|--------|--------|
| Solo DummyJSON | No se pueden provocar fallos intermitentes; CI dependería de un tercero |
| Solo simulación | Nada garantiza que se parezca a una API real |
| Fake Store API | Sin metadatos de actualización (sin marca de agua incremental) |
| WireMock / Prism | Otro servicio y otro lenguaje de configuración; el reloj de catálogo y las respuestas con estado requieren lógica igualmente |

## Consecuencias
- El job `contract` puede fallar por un corte de DummyJSON. Es intencional y **no bloquea**
  el job de Docker: un tercero caído no debe impedir validar nuestro código.
- Los endpoints `/_mock` existen solo con token; sin token devuelven 404 (seguro por defecto).
- **Hallazgo:** Starlette 1.10 depreca `httpx` en `TestClient` en favor de `httpx2` (sucesor del
  mismo autor, publicado por la organización Pydantic). Se adopta `httpx2` como cliente HTTP
  del proyecto para no mantener dos librerías.
