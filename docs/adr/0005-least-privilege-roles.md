# ADR-0005: Mínimo privilegio con roles de grupo y usuarios de servicio

- **Estado:** aceptada (implementada en fase 1)
- **Fecha:** 2026-10-27

## Decisión
- Las **migraciones** crean roles de grupo sin login (`dp_pipeline`, `dp_transform`,
  `dp_reader`) y todos los permisos. No contienen secretos, así que se versionan.
- El **bootstrap** (`python -m datapulse.db.bootstrap`) crea usuarios de login con contraseñas
  tomadas de variables de entorno y los asigna a su grupo con `SET role` por defecto.
- Las contraseñas se escapan con `quote_literal()` de PostgreSQL (los comandos `CREATE/ALTER
  ROLE` no admiten parámetros enlazados en el servidor); nunca se interpolan crudas.
- En `prod` se exigen contraseñas de 16 o más caracteres; en cualquier entorno, no vacías.

## Matriz de permisos (verificada por `tests/integration/test_privileges.py`)
| Rol            | raw            | ops                     | staging | marts          |
|----------------|----------------|-------------------------|---------|----------------|
| `dp_pipeline`  | SELECT, INSERT | SELECT, INSERT, UPDATE  | —       | —              |
| `dp_transform` | SELECT         | —                       | owner   | owner          |
| `dp_reader`    | —              | SELECT                  | —       | SELECT         |

## Consecuencias
- Si la API se ve comprometida, no puede modificar datos ni leer `raw` (datos sin minimizar).
- Los roles son objetos de **clúster**: el downgrade de la migración no los borra (pueden estar
  en uso en otra base del mismo servidor, como la de pruebas). Es una limitación documentada.
