from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from datapulse.sources.contracts import parse_page

META = {"createdAt": "2025-01-01T00:00:00.000Z", "updatedAt": "2026-01-01T00:00:00Z"}


def _page(**overrides: Any) -> dict[str, Any]:
    page: dict[str, Any] = {
        "products": [{"id": 1, "meta": META, "title": "x"}, {"id": 2, "meta": META}],
        "total": 10,
        "skip": 0,
        "limit": 2,
    }
    return page | overrides


def test_valid_page_keeps_unknown_product_fields() -> None:
    page = parse_page(_page())
    assert page.products[0].model_extra == {"title": "x"}
    assert page.products[0].meta.updated_at.year == 2026


def test_beyond_total_empty_page_is_valid() -> None:
    assert parse_page(_page(products=[], skip=500, limit=0)).products == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"limit": 3}, "limit=3"),
        ({"skip": 9}, "excede"),
        ({"extra_key": 1}, "extra"),
        ({"products": [{"id": 1}]}, "meta"),
        ({"products": [{"id": 0, "meta": META}], "limit": 1}, "greater than 0"),
        ({"total": -1}, "greater than or equal"),
    ],
)
def test_contract_violations_are_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_page(_page(**overrides))
