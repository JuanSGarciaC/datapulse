# ADR-0003: ELT por capas y propiedad de esquemas

- **Estado:** aceptada (implementada en fase 1)
- **Fecha:** 2026-10-27

## Decisión
1. **ELT con validación previa a la carga.** Python extrae, valida contra contratos y carga
   en `raw`; dbt transforma dentro de PostgreSQL (`staging` → `marts`).
2. **Una sola instancia de PostgreSQL**, separación lógica por esquemas: `raw`, `staging`,
   `marts`, `ops`.
3. **Propiedad única por esquema:** Alembic gestiona `raw` y `ops`; dbt gestiona `staging` y
   `marts`. Ninguna herramienta modifica objetos de la otra.
4. **`raw` es append-only.** Nunca se actualiza ni se borra; las correcciones llegan como
   registros nuevos y la deduplicación/“última versión” se resuelve en `staging`.
5. **Estados con `CHECK` constraints, no `ENUM`.** Añadir un valor a un `ENUM` no se puede
   revertir fácilmente; un `CHECK` se reemplaza en una migración reversible.

## Por qué
- Reprocesar es barato y seguro: `raw` conserva la historia completa, así que cualquier modelo
  de dbt se puede reconstruir con `--full-refresh` (y una prueba verificará que el resultado es
  idéntico al incremental, fase 5).
- La trazabilidad se mantiene: nada sobrescribe la evidencia de lo que llegó.

## Alternativas descartadas
- **ETL puro en Python:** transformaciones sin pruebas SQL declarativas, sin linaje ni docs.
- **Una herramienta para todo el DDL:** Alembic no modela bien vistas/modelos incrementales;
  dbt no está pensado para tablas operativas con FKs y constraints.

## Garantía automatizada
`tests/integration/test_migrations.py::test_no_schema_drift_between_code_and_migrations` y
`alembic check` en CI fallan si `schema.py` y las migraciones divergen.
