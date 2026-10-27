# ADR-0001: Prefect 3 como orquestador

- **Estado:** aceptada (implementación en fase 4)
- **Fecha:** 2026-10-27

## Contexto
Se necesita orquestación real (programación, reintentos, estados, visibilidad) para pipelines
de volumen modesto (miles a cientos de miles de filas), ejecutable en un portátil con Docker.

## Opciones
| Opción  | A favor | En contra |
|---------|---------|-----------|
| **Prefect 3** | Flujos = funciones Python con decoradores; reintentos y caché por tarea; UI incluida; 2 servicios (server + worker) | Menos extendido que Airflow en empresas grandes |
| Airflow | Estándar de facto; enorme ecosistema | Scheduler, webserver, triggerer y BD de metadatos propios; DAGs con más ceremonia; pesado para un portafolio |
| Dagster | Excelente modelo de *assets* y linaje | Curva de aprendizaje mayor; solapa con el linaje que ya aporta dbt |
| cron + scripts | Mínimo | Sin reintentos, estado ni UI: no demuestra orquestación |

## Decisión
Prefect 3. Es la alternativa **más sencilla que sigue siendo orquestación real**.

## Consecuencias
- Los flujos son testeables como funciones Python normales.
- **Verificado el 2026-10-27:** Prefect 3.9.0 exige `fastapi<0.151`. El resolvedor de uv,
  sin restricción, eligió Prefect 3.8.8 + FastAPI 0.162, combinación que **falla en tiempo de
  ejecución** (`Body() got an unexpected keyword argument 'min_items'`). Se fija
  `prefect==3.9.*` con `fastapi>=0.139,<0.151` (resuelto a 0.150.0) y se verificó ejecutando
  un flujo con reintentos. Lección: un lockfile resuelto no garantiza compatibilidad; hay que
  ejecutar.
