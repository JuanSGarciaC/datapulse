"""Generador: reproducibilidad, independencia de flujos y defectos verificables."""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from datapulse.synthetic.__main__ import main as cli
from datapulse.synthetic.generator import (
    ORPHAN_CUSTOMER_ID,
    ORPHAN_PRODUCT_ID,
    DefectKind,
    GeneratorConfig,
    check,
    generate,
    manifest,
    write,
)
from tests.conftest import ROOT

CFG = GeneratorConfig()
DS = generate(CFG)


def _csv_rows(path: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(DS.files[path])))


def _orders(path: str) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = json.loads(DS.files[path])["orders"]
    return orders


def _defects(kind: DefectKind, source: str | None = None) -> list[str]:
    return [d.key for d in DS.defects if d.kind is kind and (source is None or d.source == source)]


# ------------------------------------------------------------------ reproducibilidad
def test_same_seed_produces_identical_bytes() -> None:
    assert generate(CFG).files == DS.files


def test_different_seed_changes_content() -> None:
    assert generate(replace(CFG, seed=7)).files != DS.files


def test_streams_are_independent() -> None:
    """Más pedidos no debe alterar clientes ni productos (cada entidad tiene su RNG)."""
    other = generate(replace(CFG, orders_per_day=CFG.orders_per_day + 10))
    for path, content in DS.files.items():
        if not path.startswith("orders/"):
            assert other.files[path] == content, path


def test_committed_samples_match_generator() -> None:
    """Si alguien cambia el generador sin regenerar data/samples, esta prueba falla."""
    assert check(CFG, ROOT / "data/samples") == []


def test_written_files_use_lf_and_utf8(tmp_path: Path) -> None:
    write(CFG, tmp_path)
    for path in DS.files:
        raw = (tmp_path / path).read_bytes()
        assert b"\r\n" not in raw
        raw.decode("utf-8")
    assert check(CFG, tmp_path) == []


def test_check_reports_missing_and_modified_files(tmp_path: Path) -> None:
    write(CFG, tmp_path)
    (tmp_path / "orders/orders_batch_001.json").write_text("{}", encoding="utf-8")
    (tmp_path / "customers/customers_2026-01-03.csv").unlink()
    problems = check(CFG, tmp_path)
    assert "difiere orders/orders_batch_001.json" in problems
    assert "falta customers/customers_2026-01-03.csv" in problems


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: replace(CFG, n_days=2), id="n_days"),
        pytest.param(lambda: replace(CFG, n_customers=5), id="n_customers"),
        pytest.param(lambda: replace(CFG, orders_per_day=10), id="orders_per_day"),
    ],
)
def test_config_rejects_values_that_cannot_hold_the_defects(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=r"debe|pequeños"):
        build()


# ------------------------------------------------------------------ seguridad de datos
def test_all_emails_use_reserved_domain() -> None:
    for path in DS.files:
        if path.startswith("customers/"):
            for row in _csv_rows(path):
                if row["email"] and "@" in row["email"]:
                    assert row["email"].endswith("@example.com")


# ------------------------------------------------------------------ respuesta conocida
def test_manifest_lists_every_file_with_hash_and_rows() -> None:
    meta = manifest(CFG, DS)
    assert {f["path"] for f in meta["files"]} == set(DS.files)
    assert all(len(f["sha256"]) == 64 for f in meta["files"])
    assert sum(Counter(d["kind"] for d in meta["defects"]).values()) == len(DS.defects)


def test_customer_defects_are_really_in_the_file() -> None:
    rows = _csv_rows("customers/customers_2026-01-01.csv")
    by_id: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_id.setdefault(row["customer_id"], []).append(row)

    empty = {r["customer_id"] for r in rows if r["email"] == ""}
    assert empty == set(_defects(DefectKind.MISSING_REQUIRED, "customers_csv"))

    no_at = {r["customer_id"] for r in rows if r["email"] and "@" not in r["email"]}
    assert no_at == set(_defects(DefectKind.INVALID_FORMAT, "customers_csv"))

    bad_dates = {r["customer_id"] for r in rows if r["signup_date"] == "31/02/2026"}
    assert bad_dates == set(_defects(DefectKind.INVALID_TYPE, "customers_csv"))

    dupes = {cid for cid, rs in by_id.items() if len(rs) > 1}
    assert dupes == set(_defects(DefectKind.DUPLICATE_IN_FILE))
    assert len(rows) == CFG.n_customers + len(dupes)


def test_resend_is_byte_identical_and_schema_change_is_on_last_day() -> None:
    assert (
        DS.files["customers/customers_2026-01-02_resend.csv"]
        == DS.files["customers/customers_2026-01-02.csv"]
    )
    header_day3 = DS.files["customers/customers_2026-01-03.csv"].splitlines()[0]
    assert header_day3.endswith(",loyalty_tier")
    assert "loyalty_tier" not in DS.files["customers/customers_2026-01-02.csv"].splitlines()[0]


def test_order_defects_are_really_in_the_batches() -> None:
    b1 = _orders("orders/orders_batch_001.json")
    b2 = _orders("orders/orders_batch_002.json")
    b3 = _orders("orders/orders_batch_003.json")
    b1_by_id = {o["order_id"]: o for o in b1}

    dupes = [o["order_id"] for o in b2 if o["order_id"] in b1_by_id]
    assert sorted(dupes) == sorted(_defects(DefectKind.DUPLICATE_ACROSS_BATCHES))
    assert all(o == b1_by_id[o["order_id"]] for o in b2 if o["order_id"] in b1_by_id)

    late = [o for o in b3 if o["order_id"] in b1_by_id]
    assert sorted(o["order_id"] for o in late) == sorted(_defects(DefectKind.LATE_UPDATE))
    for order in late:
        assert order["updated_at"] > b1_by_id[order["order_id"]]["updated_at"]
        assert order["status"] in {"returned", "cancelled"}

    bad_qty = [o["order_id"] for o in b1 if o["items"] and o["items"][0]["quantity"] == "two"]
    assert sorted(bad_qty) == sorted(_defects(DefectKind.INVALID_TYPE, "orders_json"))
    assert [o["order_id"] for o in b1 if not o["items"]] == _defects(DefectKind.EMPTY_COLLECTION)

    null_customer = [o["order_id"] for o in b2 if o["customer_id"] is None]
    assert sorted(null_customer) == sorted(_defects(DefectKind.MISSING_REQUIRED, "orders_json"))

    non_positive = [
        o["order_id"]
        for o in b2
        if isinstance(o["items"][0]["quantity"], int) and o["items"][0]["quantity"] <= 0
    ]
    assert sorted(non_positive) == sorted(_defects(DefectKind.OUT_OF_RANGE, "orders_json"))

    orphans = [o["order_id"] for o in b3 if o["customer_id"] == ORPHAN_CUSTOMER_ID] + [
        o["order_id"] for o in b3 if any(i["product_id"] == ORPHAN_PRODUCT_ID for i in o["items"])
    ]
    assert sorted(orphans) == sorted(_defects(DefectKind.ORPHAN_REFERENCE))


def test_product_defects_and_versions() -> None:
    versions = json.loads(DS.files["products/products_catalog.json"])["versions"]
    first: dict[int, dict[str, Any]] = {}
    for v in versions:
        first.setdefault(v["id"], v)
    null_price = {str(pid) for pid, v in first.items() if v["price"] is None}
    empty_title = {str(pid) for pid, v in first.items() if v["title"] == ""}
    negative = {str(pid) for pid, v in first.items() if v["price"] is not None and v["price"] < 0}
    missing = set(_defects(DefectKind.MISSING_REQUIRED, "products_api"))
    assert null_price | empty_title == missing
    assert negative == set(_defects(DefectKind.OUT_OF_RANGE, "products_api"))
    assert len(first) == DS.expectations["products_distinct"] == CFG.n_products + CFG.n_new_products
    assert len(versions) == DS.expectations["product_versions"]


def test_expectations_match_content() -> None:
    order_ids = {
        o["order_id"] for path in DS.files if path.startswith("orders/") for o in _orders(path)
    }
    assert len(order_ids) == DS.expectations["orders_distinct"]
    customer_ids = {
        r["customer_id"]
        for path in DS.files
        if path.startswith("customers/")
        for r in _csv_rows(path)
    }
    assert len(customer_ids) == DS.expectations["customers_distinct"]


# ------------------------------------------------------------------ CLI
def test_cli_generate_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(["--out", str(tmp_path)]) == 0
    assert "duplicate_in_file" in capsys.readouterr().out
    assert cli(["--out", str(tmp_path), "--check"]) == 0
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "orders/orders_batch_002.json").unlink()
    assert cli(["--out", str(tmp_path), "--check"]) == 1
    assert "falta orders/orders_batch_002.json" in capsys.readouterr().err
