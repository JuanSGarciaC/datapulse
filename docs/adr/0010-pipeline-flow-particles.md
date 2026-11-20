# ADR-0010: Registros que fluyen por el mapa del pipeline

- **Estado:** aceptada
- **Fecha:** 2026-11-20

## Contexto
El mapa del pipeline ([ADR-0009](0009-dashboard-visual-system-and-trigger.md)) muestra el estado
de cada etapa, pero sus aristas animadas solo dicen «por aquí pasan datos». Lo que distingue a
este pipeline (la validación que manda filas a cuarentena antes de cargarlas) no se veía.

## Decisiones
1. **Una capa de partículas en `<canvas>` sobre el SVG** (`dashboard/flow.py` y `flow.js`, sin
   dependencias). Cada partícula es un paquete de registros: cada fuente emite con un ritmo
   proporcional a la raíz de sus filas leídas en la última ejecución; en «Validación», una
   parte igual a su tasa de rechazo se desvía en rojo a una **bandeja de cuarentena**; en
   dbt → marts, la parte con aviso sale en ámbar. Una etapa en curso emite más rápido.
2. **El SVG sigue siendo la verdad.** La bandeja de cuarentena, con su número de filas, se
   dibuja en el SVG del servidor; las partículas no añaden información que falte sin JavaScript.
   `pipeline_map.flow_model` pasa al navegador solo números y claves fijas, nunca texto de la API.
3. **Componente v2 de Streamlit (`st.components.v2`), sin iframe.** Los datos del refresco de
   5 s llegan sin volver a montarlo: las partículas no se reinician. Un iframe de
   `components.v1.html` se recargaría en cada refresco.
4. **Acompañan dos efectos ligeros** en `theme.py`: las cifras de las tarjetas cuentan hasta su
   valor la primera vez que se ven, y los gráficos entran con un barrido. Ambos se activan
   cuando el elemento entra en pantalla.

## Reglas de movimiento
- `prefers-reduced-motion`: el lienzo no arranca, las cifras y los gráficos aparecen directamente.
- Se pausa fuera de la vista (`IntersectionObserver`) y con la pestaña oculta (`visibilitychange`).
- Móvil (< 600 px o puntero táctil): como mucho 60 partículas, sin estelas y a densidad 1.
- Los guiones animados del SVG solo se apagan cuando el lienzo pintó su primer fotograma; si el
  bucle falla o deja de pintar 1,5 s estando visible, se retira y vuelven los guiones.

## Alternativas descartadas
| Opción | Motivo |
|--------|--------|
| Partículas como fondo (atmósfera) o un shader | Competirían con los datos en una herramienta de trabajo; aquí el movimiento informa. |
| Reconstrucción con enjambre de cursores al entrar | Un panel que se abre a diario no debe hacer esperar. |
| `components.v1.html` (iframe) | Se recarga con cada refresco del mapa y aísla el lienzo del SVG. |
| Librería de animación | Siete curvas y unas decenas de puntos caben en un `canvas` 2D. |

## Consecuencias
- El lienzo se coloca dentro de la tarjeta `.st-key-dp-map`, igual que el CSS de ADR-0009
  depende de clases de Streamlit: si cambian, el mapa se ve como antes, sin partículas.
- El JavaScript del componente es código fijo del paquete; los datos son números.
