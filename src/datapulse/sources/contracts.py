"""Contrato de la API de productos (forma de DummyJSON, verificada en vivo el 2026-10-27).

Este contrato valida la **envoltura** de la respuesta y las claves mínimas que el
pipeline necesita (``id`` y ``meta.updatedAt``), no la calidad fila a fila: un
precio nulo es un problema de calidad (fase 3), no un cambio de contrato.

Lo usan tanto la prueba de contrato contra la API real como la API simulada,
así se garantiza que la simulación no se aleja de la realidad.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegative = Annotated[int, Field(ge=0)]


class ProductMeta(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class RawProduct(BaseModel):
    """Claves mínimas de un producto. El resto se conserva tal cual (``extra=allow``)."""

    model_config = ConfigDict(extra="allow")

    id: Annotated[int, Field(gt=0)]
    meta: ProductMeta


class ProductPage(BaseModel):
    """Página de ``GET /products?limit=&skip=``."""

    model_config = ConfigDict(extra="forbid")

    products: list[RawProduct]
    total: NonNegative
    skip: NonNegative
    limit: NonNegative

    @model_validator(mode="after")
    def _consistent(self) -> ProductPage:
        # DummyJSON devuelve en ``limit`` el número de elementos realmente enviados.
        if self.limit != len(self.products):
            raise ValueError(f"limit={self.limit} pero se recibieron {len(self.products)}")
        # Pedir más allá del final es válido (lista vacía con skip > total), visto en vivo.
        if self.products and self.skip + len(self.products) > self.total:
            raise ValueError("la página excede el total declarado")
        return self


class ErrorBody(BaseModel):
    """Errores de validación de DummyJSON: ``{"message": "..."}``."""

    message: str


def parse_page(payload: dict[str, Any]) -> ProductPage:
    return ProductPage.model_validate(payload)
