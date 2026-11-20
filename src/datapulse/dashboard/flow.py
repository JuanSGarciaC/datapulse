"""Capa de partículas sobre el mapa del pipeline (componente v2 de Streamlit, sin iframe).

El componente no pinta nada propio: su JavaScript (``flow.js``) coloca un ``<canvas>`` sobre
el SVG del mapa y recibe ``pipeline_map.flow_model`` como datos. Al ser un componente v2,
los datos nuevos del refresco de 5 s llegan sin volver a montarlo y las partículas no se
reinician. Si el JavaScript no arranca, el SVG sigue igual que sin esta capa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import streamlit as st

JS: Final = Path(__file__).with_name("flow.js").read_text(encoding="utf-8")
# El contenedor del componente no ocupa sitio: el lienzo vive dentro de la tarjeta del mapa.
CSS: Final = ":host{display:block;height:0;overflow:hidden}"

_mount = st.components.v2.component("dp_pipeline_flow", js=JS, css=CSS)


def mount(model: dict[str, Any]) -> None:
    _mount(data=model, key="dp-flow", height=0)
