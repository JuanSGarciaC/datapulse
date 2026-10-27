# ADR-0002: Polars para extracción y validación

- **Estado:** aceptada (implementación en fase 3)
- **Fecha:** 2026-10-27

## Contexto
La capa de validación previa a la carga debe detectar tipos incorrectos, nulos y duplicados
**sin que la librería los “arregle” en silencio**.

## Decisión
Polars 2.x con esquemas explícitos (`schema=`/`schema_overrides=`) y lectura estricta.

## Por qué no Pandas
- Inferencia laxa: un entero con nulos pasa a `float64`; columnas mixtas pasan a `object`.
  Justo lo contrario de lo que necesita una capa de calidad.
- Polars falla de forma explícita ante un tipo inválido y permite `strict=False` controlado
  para separar filas válidas de inválidas (cuarentena).
- Rendimiento y memoria mejores (motor columnar en Rust), aunque con este volumen no es el
  motivo principal.

## Consecuencias
- El equipo debe conocer la API de expresiones de Polars.
- La transformación analítica **no** se hace en Polars sino en SQL con dbt (ADR-0003).
