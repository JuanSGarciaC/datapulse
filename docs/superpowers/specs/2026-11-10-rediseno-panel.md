# Rediseño del panel (fase 8)

- **Fecha:** 2026-11-10
- **Rama:** `rediseno-panel`

## Objetivo

Que el panel muestre todo lo que la API ya ofrece y que se vea como un producto: oscuro,
con profundidad y movimiento que significa algo. Sin frontend nuevo y sin endpoints nuevos.

## Decisiones

| Decisión | Motivo |
|----------|--------|
| Seguir en Streamlit | Un frontend aparte duplica 600 líneas probadas y añade Node, otra imagen y otro job de CI. El proyecto demuestra ingeniería de datos, no React. |
| Mapa del pipeline como SVG generado en Python | Función pura (datos → HTML escapado), se prueba sin navegador. Sin arrastre ni zoom: son 7 nodos que caben en pantalla. |
| Disparo de pipelines opcional | Sin `DATAPULSE_DASHBOARD_ADMIN_KEY` el botón no existe y el panel sigue siendo de solo lectura. Ver [ADR-0009](../../adr/0009-dashboard-visual-system-and-trigger.md). |
| Sin dependencias nuevas | Streamlit, Altair y CSS bastan. |

## Alcance

### 1. Sistema visual (`dashboard/theme.py`)
- Tema oscuro de Streamlit (`--theme.*`) más una hoja CSS: fondo con degradado, tarjetas con
  profundidad y borde iluminado que gira sin parar.
- El brillo de cada tarjeta sigue al puntero (unas líneas de JavaScript estático).
- `prefers-reduced-motion` apaga las animaciones.
- Gráficos Altair con paleta para fondo oscuro. Sin emojis: los estados van con texto y color.

### 2. Dar pantalla a lo que la API ya ofrece
| API | Dónde |
|-----|-------|
| `/v1/ingestion/batches` | Pipelines → «Lotes de ingesta» |
| Detalle de ejecución (`trigger`, `quality_failures`, `params`, `orchestrator_run_id`) | Pipelines → detalle |
| `/v1/products/top?metric=` | Negocio → selector ingresos/unidades |
| `/v1/sales/daily?category=` | Negocio → filtro de categoría |
| `/v1/quality/summary?layer=` | Calidad → selector de capa |
| `/v1/pipeline-runs?status=` | Pipelines → filtro de estado |
| KPI `units` | Negocio → tarjeta «Unidades» |

### 3. Mapa del pipeline (`dashboard/pipeline_map.py`)
Fuentes (clientes, pedidos, productos) → validación → `raw` → dbt → marts. Cada nodo muestra
el estado de su última ejecución, filas y duración; las aristas fluyen y, si hay una ejecución
en curso, el nodo late. Al pasar el puntero por un nodo se ve su detalle.

### 4. Disparo opcional
Botones «Ejecutar flujo diario» y «Ejecutar ingesta de productos» (los dos despliegues de la
lista cerrada de la API). Tras disparar, el mapa se refresca solo cada 5 s mientras haya una
ejecución en curso o durante 60 s.

## Fuera de alcance
Frontend aparte, arrastre y zoom, endpoints nuevos, login en el panel.

## Plan
1. Cliente: `batches`, `trigger`, parámetros `metric`, `category`, `status`, clave admin opcional.
2. `pipeline_map.render` con pruebas (estados, escape de HTML, sin datos).
3. `theme.py`, paleta oscura en `charts.py`, arranque con tema oscuro.
4. Página: mapa, filtros nuevos, lotes, detalle ampliado, disparo y refresco.
5. Compose, `.env.example`, `demo.ps1`.
6. E2E contra el stack en Docker, capturas, documentación y README.
