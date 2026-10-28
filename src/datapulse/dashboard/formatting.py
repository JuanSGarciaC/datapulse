"""Formato de cifras para el dashboard (sin dependencias de Streamlit: importable en pruebas).

``app.py`` no se puede importar sin ejecutarse, porque Streamlit lo corre como script.
"""

from __future__ import annotations

from typing import Any


def money(value: Any) -> str:
    return f"$ {float(value):,.2f}" if value is not None else "—"


def money_compact(value: Any) -> str:
    """Cifra corta para tarjetas (no se trunca); el valor exacto va en la ayuda."""
    if value is None:
        return "—"
    amount = float(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(amount) >= limit:
            return f"$ {amount / limit:,.2f} {suffix}"
    return f"$ {amount:,.2f}"
