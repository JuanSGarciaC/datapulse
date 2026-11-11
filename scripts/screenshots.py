"""Regenera las capturas del panel en docs/img y comprueba el acabado visual en un navegador.

Requiere el stack levantado (scripts/demo.ps1). Usa el Edge o Chrome ya instalado:

    uv run --with playwright python scripts/screenshots.py

Además de las capturas graba docs/img/dashboard-demo.gif: el puntero recorre el mapa y, si el
panel tiene la clave de administración, dispara el flujo diario.

Falla si el mapa no se dibuja, si el CSS no llega a las tarjetas o si el brillo no sigue al
puntero: así una actualización de Streamlit que cambie sus selectores no pasa inadvertida.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

URL = os.environ.get("DATAPULSE_E2E_DASHBOARD_URL", "http://127.0.0.1:8501")
OTHERS = {
    "prefect-runs": os.environ.get("DATAPULSE_E2E_PREFECT_URL", "http://127.0.0.1:4200") + "/runs",
    "api-swagger": os.environ.get("DATAPULSE_E2E_API_URL", "http://127.0.0.1:8000") + "/docs",
}
GIF_WIDTH = 800
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


def record_demo(page: Page) -> None:
    """GIF del mapa: el puntero pasa por cada nodo y después se dispara el flujo diario."""
    area = page.locator(".st-key-dp-map")
    area.evaluate("e => e.scrollIntoView({block: 'start'})")
    frames: list[Image.Image] = []

    def snap(count: int, pause_ms: int) -> None:
        for _ in range(count):
            page.wait_for_timeout(pause_ms)
            shot = Image.open(io.BytesIO(area.screenshot())).convert("RGB")
            frames.append(shot.resize((GIF_WIDTH, round(shot.height * GIF_WIDTH / shot.width))))

    for key in NODES:
        page.locator(f"svg.dp-map .n-{key} .box").hover(force=True)
        snap(3, 250)
    button = page.get_by_role("button", name="Ejecutar flujo diario")
    if button.count():
        button.click()
        page.locator("svg.dp-map .n-dbt .box").hover(force=True)
        snap(36, 500)
    size = frames[0].size
    # Paleta común: sin ella cada fotograma guarda la suya y el GIF pesa siete veces más.
    palette = frames[-1].quantize(colors=128, dither=Image.Dither.NONE)
    reduced = [
        (f if f.size == size else f.resize(size)).quantize(
            palette=palette, dither=Image.Dither.NONE
        )
        for f in frames
    ]
    reduced[0].save(
        OUT / "dashboard-demo.gif",
        save_all=True,
        append_images=reduced[1:],
        duration=350,
        loop=0,
        optimize=True,
    )


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

        # La pestaña Pipelines sigue abierta: ejecuciones, detalle y lotes de ingesta.
        page.get_by_text("Ejecuciones recientes").evaluate(
            "e => e.scrollIntoView({block: 'start'})"
        )
        page.wait_for_timeout(1500)
        page.screenshot(path=OUT / "dashboard-lotes.png")
        record_demo(page)
        for name, url in OTHERS.items():
            other = browser.new_page(viewport={"width": 1400, "height": 900})
            other.goto(url)
            other.wait_for_timeout(4000)
            other.screenshot(path=OUT / f"{name}.png")

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
