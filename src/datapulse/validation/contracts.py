"""Contratos de datos versionados en código (uno por fuente).

Cambiar un contrato es un cambio de código revisable, no una configuración
oculta. Los nombres de reglas resultantes (``not_null:email``…) son los que
aparecen en ``ops.quality_results`` y en el dashboard.
"""

from __future__ import annotations

from typing import Any, Final

from datapulse.synthetic import vocab
from datapulse.validation.tabular import ColumnSpec, Kind, TabularContract

EMAIL: Final = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

CUSTOMERS: Final = TabularContract(
    source="customers_csv",
    key="customer_id",
    columns=(
        ColumnSpec("customer_id", required=True, pattern=r"^C\d{5}$"),
        ColumnSpec("first_name"),
        ColumnSpec("last_name"),
        ColumnSpec("email", required=True, pattern=EMAIL),
        ColumnSpec("city"),
        ColumnSpec("country", pattern=r"^[A-Z]{2}$"),
        ColumnSpec("segment", allowed=frozenset(vocab.SEGMENTS)),
        ColumnSpec("signup_date", kind=Kind.DATE),
        ColumnSpec("updated_at", kind=Kind.DATETIME, required=True),
    ),
)

PRODUCTS: Final = TabularContract(
    source="products_api",
    key="id",
    columns=(
        ColumnSpec("id", kind=Kind.INT, required=True, min_value=1, target="product_id"),
        ColumnSpec("title", required=True),
        ColumnSpec("description"),
        ColumnSpec("category", required=True),
        ColumnSpec("brand"),
        ColumnSpec("sku"),
        ColumnSpec("price", kind=Kind.DECIMAL, required=True, min_value=0),
        ColumnSpec(
            "discountPercentage",
            kind=Kind.DECIMAL,
            min_value=0,
            max_value=100,
            target="discount_percentage",
        ),
        ColumnSpec("rating", kind=Kind.DECIMAL, min_value=0, max_value=5),
        ColumnSpec("stock", kind=Kind.INT, min_value=0),
        ColumnSpec("availabilityStatus", target="availability_status"),
        ColumnSpec("createdAt", kind=Kind.DATETIME, target="created_at"),
        ColumnSpec("updatedAt", kind=Kind.DATETIME, required=True, target="updated_at"),
    ),
)

_PRODUCT_FIELDS: Final = frozenset(PRODUCTS.names)


def flatten_product(record: dict[str, Any]) -> tuple[dict[str, str | None], dict[str, Any]]:
    """Separa un producto de la API en (campos del contrato como texto, resto en ``_extra``).

    ``meta.createdAt``/``meta.updatedAt`` suben al primer nivel; imágenes, reseñas,
    dimensiones, etc. se conservan íntegras en ``_extra`` (nada se descarta).
    """
    flat = dict(record)
    meta = flat.pop("meta", None)
    extra: dict[str, Any] = {}
    if isinstance(meta, dict):
        flat["createdAt"] = meta.get("createdAt")
        flat["updatedAt"] = meta.get("updatedAt")
        rest = {k: v for k, v in meta.items() if k not in {"createdAt", "updatedAt"}}
        if rest:
            extra["meta"] = rest
    known: dict[str, str | None] = {}
    for name in PRODUCTS.names:
        value = flat.pop(name, None)
        known[name] = None if value is None else str(value)
    extra |= flat
    return known, extra
