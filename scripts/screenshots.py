"""Regenera las capturas del panel en docs/img y comprueba el acabado visual en un navegador.

Requiere el stack levantado (scripts/demo.ps1). Usa el Edge o Chrome ya instalado:

    uv run --with playwright python scripts/screenshots.py

Falla si el mapa no se dibuja, si el CSS no llega a las tarjetas o si el brillo no sigue al
puntero: así una actualización de Streamlit que cambie sus selectores no pasa inadvertida.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

URL = os.environ.get("DATAPULSE_E2E_DASHBOARD_URL", "http://127.0.0.1:8501")
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
NODES = ("customers", "orders", "products", "validation", "raw", "dbt", "marts")
CARD = '[data-testid="stMetric"]'
TABS = {"Negocio": "negocio", "Calidad de datos": "calidad", "Pipelines": "pipelines"}


def check(condition: object, message: str) -> None:
    if not condition:
        raise SystemExit(f"FALLO: {message}")


def verify(page: Page) -> None:
    check(page.locator("svg.dp-map .node").count() == len(NODES), "faltan nodos en el mapa")
    check(page.locator("svg.dp-map .edge.flowing").count() > 0, "ninguna arista fluye")
    card = page.locator(CARD).first
    border = card.evaluate("e => getComputedStyle(e, '::before').animationName")
    check(border == "dp-spin", f"el borde iluminado no se aplica a las tarjetas ({border})")
    card.hover()
    check(card.evaluate("e => e.style.getPropertyValue('--mx')"), "el brillo no sigue al puntero")
    node = page.locator("svg.dp-map .node").first
    node.locator(".box").hover()
    page.wait_for_timeout(400)  # la transición del detalle
    opacity = node.locator(".detail").evaluate("e => getComputedStyle(e).opacity")
    check(opacity == "1", "el detalle del nodo no aparece al pasar el puntero")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("DATAPULSE_BROWSER", "msedge"))
        page = browser.new_page(viewport={"width": 1400, "height": 1500})
        page.goto(URL)
        page.wait_for_selector("svg.dp-map")
        page.wait_for_selector(CARD)
        verify(page)
        page.locator(".st-key-dp-map").screenshot(path=OUT / "dashboard-mapa.png")
        for tab, name in TABS.items():
            page.get_by_role("tab", name=tab).click()
            page.wait_for_timeout(2500)  # los gráficos de Vega terminan de dibujarse
            page.mouse.move(0, 0)
            page.screenshot(path=OUT / f"dashboard-{name}.png")

        # Con movimiento reducido las animaciones se apagan.
        calm = browser.new_page(reduced_motion="reduce")
        calm.goto(URL)
        calm.wait_for_selector(CARD)
        still = calm.locator(CARD).first.evaluate(
            "e => getComputedStyle(e, '::before').animationName"
        )
        check(still == "none", "prefers-reduced-motion no apaga el borde animado")
        browser.close()
    print(f"Capturas regeneradas en {OUT}")


if __name__ == "__main__":
    main()
