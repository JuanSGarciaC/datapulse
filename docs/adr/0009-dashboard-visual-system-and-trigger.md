# ADR-0009: Sistema visual del panel, mapa del pipeline y disparo opcional

- **Estado:** aceptada (implementada en fase 8)
- **Fecha:** 2026-11-10

## Contexto
El panel funcionaba pero mostraba menos de lo que la API ofrece (los lotes de ingesta no
tenían pantalla) y se veía como un Streamlit por defecto. Un panel de portafolio debe enseñar
el pipeline de un vistazo y, si se puede, verlo ejecutarse.

## Decisiones
1. **Seguir en Streamlit** y darle un sistema visual propio con una hoja CSS y unas líneas de
   JavaScript estático (`dashboard/theme.py`).
2. **El mapa del pipeline es SVG generado por una función pura** (`dashboard/pipeline_map.py`):
   datos de la API → HTML. El movimiento informa: una arista fluye solo si su etapa de origen
   tiene datos, y un nodo late solo mientras su flujo está en curso.
3. **El disparo de pipelines desde el panel es opcional y viene apagado.** Solo aparece si se
   define `DATAPULSE_DASHBOARD_ADMIN_KEY`. Sin ella el panel sigue siendo un cliente de solo
   lectura, como establece [ADR-0005](0005-least-privilege-roles.md).
4. **Tras disparar, el mapa se refresca cada 5 s** mientras haya una ejecución en curso o
   durante 60 s; después el refresco se apaga. Una ejecución «en curso» de más de 15 minutos
   se trata como huérfana y no lo mantiene encendido.

## Alternativas descartadas
| Opción | Motivo |
|--------|--------|
| Frontend aparte (React) | Duplica un panel de 600 líneas ya probado y añade Node, otra imagen y otro job de CI. No aporta nada al objetivo del proyecto, que es ingeniería de datos. |
| Lienzo con arrastre y zoom | Son siete nodos que caben en pantalla; exigiría una librería de JavaScript y un componente propio. |
| Disparo siempre activo | Obligaría a dar al panel la clave de administración en todos los despliegues. |
| Refresco permanente | Tres consultas cada 5 s sin que nada cambie. |

## Consecuencias
- **El CSS depende de los `data-testid` de Streamlit**, que no son API pública. Si una versión
  los cambia, el panel sigue funcionando pero pierde el acabado. Las capturas de `docs/img`
  se regeneran al actualizar Streamlit para detectarlo.
- **Con la clave de administración, quien abra el panel puede disparar pipelines**, porque el
  panel no tiene login. El riesgo está acotado: el panel solo escucha en `127.0.0.1`, la API
  solo admite dos despliegues de una lista cerrada, rechaza `full_refresh` y las cargas son
  idempotentes. No se debe activar si el panel se publica en una red compartida.
- Todo texto de la API que entra en el SVG pasa por `html.escape`; el JavaScript es un texto
  fijo sin datos interpolados.
- Las animaciones se apagan con `prefers-reduced-motion`.
