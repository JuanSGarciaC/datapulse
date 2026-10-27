"""Generador determinista de datos sintéticos de e-commerce con defectos documentados.

Propiedades garantizadas (y probadas en ``tests/unit/test_synthetic.py``):

* **Reproducible:** misma configuración y semilla → mismos bytes en cada archivo.
* **Flujos independientes:** cada entidad usa su propio generador aleatorio
  derivado de la semilla, así que cambiar el número de pedidos no altera los clientes.
* **Respuesta conocida:** cada defecto inyectado queda registrado en ``manifest.json``
  para que las validaciones de la fase 3 se prueben contra conteos exactos.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from datapulse.synthetic import vocab

GENERATOR_VERSION: Final = "1"
CUSTOMER_COLUMNS: Final = (
    "customer_id",
    "first_name",
    "last_name",
    "email",
    "city",
    "country",
    "segment",
    "signup_date",
    "updated_at",
)
ORPHAN_PRODUCT_ID: Final = 9999
ORPHAN_CUSTOMER_ID: Final = "C99999"


class DefectKind(StrEnum):
    DUPLICATE_IN_FILE = "duplicate_in_file"
    DUPLICATE_ACROSS_BATCHES = "duplicate_across_batches"
    IDENTICAL_FILE_RESEND = "identical_file_resend"
    LATE_UPDATE = "late_update"
    MISSING_REQUIRED = "missing_required"
    INVALID_TYPE = "invalid_type"
    INVALID_FORMAT = "invalid_format"
    OUT_OF_RANGE = "out_of_range"
    ORPHAN_REFERENCE = "orphan_reference"
    EMPTY_COLLECTION = "empty_collection"
    SCHEMA_CHANGE = "schema_change"


@dataclass(frozen=True, slots=True)
class Defect:
    source: str
    file: str
    kind: DefectKind
    key: str
    field: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    seed: int = 42
    n_customers: int = 200
    new_customers_per_day: int = 15
    changed_customers_per_day: int = 20
    n_products: int = 194  # igual que el catálogo real de DummyJSON
    n_new_products: int = 6
    changed_products_per_day: int = 15
    n_days: int = 3
    orders_per_day: int = 150
    start: date = date(2026, 1, 1)

    def __post_init__(self) -> None:
        if self.n_days < 3:  # noqa: PLR2004 - los defectos se reparten en 3 lotes
            raise ValueError("n_days debe ser >= 3")
        if min(self.n_customers, self.n_products, self.orders_per_day) < 30:  # noqa: PLR2004
            raise ValueError("volúmenes demasiado pequeños para inyectar los defectos")


@dataclass(slots=True)
class Dataset:
    """Resultado en memoria: archivo relativo → contenido en texto, más los defectos."""

    files: dict[str, str] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    defects: list[Defect] = field(default_factory=list)
    expectations: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- utilidades
def _rng(seed: int, stream: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{stream}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))  # noqa: S311  # nosec B311 - reproducibilidad, no criptografía


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _at(day: date, minutes: int) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC) + timedelta(minutes=minutes)


def _ascii(text: str) -> str:
    table = str.maketrans("áéíóúÁÉÍÓÚñÑüÜ", "aeiouAEIOUnNuU")
    return text.translate(table).lower().replace(" ", "")


def _csv(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    # lineterminator fijo: el mismo archivo en Windows y Linux (hash estable).
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- productos
def _base_product(rng: random.Random, pid: int, created: datetime) -> dict[str, Any]:
    category = rng.choice(vocab.CATEGORIES)
    adjective = rng.choice(vocab.PRODUCT_ADJECTIVES)
    title = f"{adjective} {category.replace('-', ' ').title()} {pid:03d}"
    price = round(rng.uniform(4.99, 1999.99), 2)
    return {
        "id": pid,
        "title": title,
        "description": f"{title}: producto sintético para demostraciones de DataPulse.",
        "category": category,
        "price": price,
        "discountPercentage": round(rng.uniform(0, 20), 2),
        "rating": round(rng.uniform(2.5, 5.0), 2),
        "stock": rng.randint(0, 250),
        "brand": f"Marca {rng.randint(1, 25):02d}",
        "sku": f"{category[:3].upper()}-{pid:05d}",
        "availabilityStatus": "In Stock",
        "meta": {
            "createdAt": _iso(created),
            "updatedAt": _iso(created),
            "barcode": f"{rng.randint(10**12, 10**13 - 1)}",
        },
    }


def _products(cfg: GeneratorConfig, ds: Dataset) -> dict[int, float]:
    """Genera versiones del catálogo y devuelve el precio “real” vigente al día 1."""
    rng = _rng(cfg.seed, "products")
    epoch = _at(cfg.start - timedelta(days=200), 0)
    versions: list[dict[str, Any]] = []
    current: dict[int, dict[str, Any]] = {}
    for pid in range(1, cfg.n_products + 1):
        product = _base_product(rng, pid, epoch + timedelta(hours=rng.randint(0, 24 * 180)))
        current[pid] = product
        versions.append(product)
    true_prices = {pid: float(p["price"]) for pid, p in current.items()}

    # Días 2..n: cambios de precio/stock (para la carga incremental) y productos nuevos.
    next_id = cfg.n_products + 1
    for offset in range(1, cfg.n_days):
        day = cfg.start + timedelta(days=offset)
        for pid in sorted(rng.sample(sorted(current), cfg.changed_products_per_day)):
            updated = json.loads(json.dumps(current[pid]))
            updated["price"] = round(float(updated["price"]) * rng.uniform(0.8, 1.2), 2)
            updated["stock"] = rng.randint(0, 250)
            updated["meta"]["updatedAt"] = _iso(_at(day, rng.randint(60, 1380)))
            current[pid] = updated
            versions.append(updated)
        if offset == cfg.n_days - 1:
            for _ in range(cfg.n_new_products):
                product = _base_product(rng, next_id, _at(day, rng.randint(60, 1380)))
                current[next_id] = product
                versions.append(product)
                next_id += 1

    # Defectos en la primera versión de algunos productos.
    file = "products/products_catalog.json"
    first_versions = {v["id"]: v for v in reversed(versions)}  # primera aparición por id
    victims = rng.sample(range(1, cfg.n_products + 1), 4)
    for pid in victims[:2]:
        first_versions[pid]["price"] = None
        ds.defects.append(
            Defect(
                "products_api", file, DefectKind.MISSING_REQUIRED, str(pid), "price", "price=null"
            )
        )
    first_versions[victims[2]]["price"] = -abs(float(first_versions[victims[2]]["price"]))
    ds.defects.append(
        Defect(
            "products_api",
            file,
            DefectKind.OUT_OF_RANGE,
            str(victims[2]),
            "price",
            "precio negativo",
        )
    )
    first_versions[victims[3]]["title"] = ""
    ds.defects.append(
        Defect(
            "products_api",
            file,
            DefectKind.MISSING_REQUIRED,
            str(victims[3]),
            "title",
            "title vacío",
        )
    )

    versions.sort(key=lambda v: (v["id"], v["meta"]["updatedAt"]))
    ds.files[file] = _json(
        {"generator_version": GENERATOR_VERSION, "seed": cfg.seed, "versions": versions}
    )
    ds.rows[file] = len(versions)
    ds.expectations["products_distinct"] = len(current)
    ds.expectations["product_versions"] = len(versions)
    return true_prices


# --------------------------------------------------------------------------- clientes
def _new_customer(rng: random.Random, n: int, signup: date, updated: datetime) -> dict[str, Any]:
    first = rng.choice(vocab.FIRST_NAMES)
    last = rng.choice(vocab.LAST_NAMES)
    city, country = rng.choice(vocab.CITIES)
    return {
        "customer_id": f"C{n:05d}",
        "first_name": first,
        "last_name": last,
        # Dominio reservado por RFC 2606: imposible que sea un correo real.
        "email": f"{_ascii(first)}.{_ascii(last)}.{n}@example.com",
        "city": city,
        "country": country,
        "segment": rng.choice(vocab.SEGMENTS),
        "signup_date": signup.isoformat(),
        "updated_at": _iso(updated),
    }


def _inject_customer_defects(
    rng: random.Random, rows: list[dict[str, Any]], file: str, ds: Dataset
) -> list[dict[str, Any]]:
    """Altera filas del CSV del día 1 y registra cada defecto. Devuelve filas + duplicados."""

    def record(kind: DefectKind, key: str, field_name: str | None, detail: str) -> None:
        ds.defects.append(Defect("customers_csv", file, kind, key, field_name, detail))

    pool = rng.sample(range(len(rows)), 10)
    for i in pool[0:3]:
        rows[i]["email"] = ""
        record(DefectKind.MISSING_REQUIRED, rows[i]["customer_id"], "email", "email vacío")
    for i in pool[3:5]:
        rows[i]["signup_date"] = "31/02/2026"
        record(DefectKind.INVALID_TYPE, rows[i]["customer_id"], "signup_date", "fecha imposible")
    for i in pool[5:7]:
        rows[i]["email"] = rows[i]["email"].replace("@", ".at.")
        record(DefectKind.INVALID_FORMAT, rows[i]["customer_id"], "email", "email sin @")
    duplicated = [dict(rows[i]) for i in pool[7:10]]
    for row in duplicated:
        record(DefectKind.DUPLICATE_IN_FILE, row["customer_id"], None, "fila repetida")
    return rows + duplicated


def _customers(cfg: GeneratorConfig, ds: Dataset) -> list[list[str]]:
    """Genera los CSV diarios y devuelve los ids de cliente disponibles por día."""
    rng = _rng(cfg.seed, "customers")
    day1 = cfg.start
    customers: dict[str, dict[str, Any]] = {}
    for n in range(1, cfg.n_customers + 1):
        signup = day1 - timedelta(days=rng.randint(1, 900))
        c = _new_customer(rng, n, signup, _at(day1, rng.randint(0, 600)))
        customers[c["customer_id"]] = c

    # Día 1: exportación completa con defectos.
    f1 = f"customers/customers_{day1.isoformat()}.csv"
    rows1 = _inject_customer_defects(rng, [dict(c) for c in customers.values()], f1, ds)
    ds.files[f1] = _csv(CUSTOMER_COLUMNS, rows1)
    ds.rows[f1] = len(rows1)

    available: list[list[str]] = [sorted(customers)]
    next_n = cfg.n_customers + 1
    changes_total = 0
    for offset in range(1, cfg.n_days):
        day = cfg.start + timedelta(days=offset)
        delta: list[dict[str, Any]] = []
        for cid in sorted(rng.sample(sorted(customers), cfg.changed_customers_per_day)):
            changed = dict(customers[cid])
            changed["city"], changed["country"] = rng.choice(vocab.CITIES)
            changed["segment"] = rng.choice(vocab.SEGMENTS)
            changed["updated_at"] = _iso(_at(day, rng.randint(0, 600)))
            customers[cid] = changed
            delta.append(dict(changed))
        changes_total += cfg.changed_customers_per_day
        for _ in range(cfg.new_customers_per_day):
            c = _new_customer(rng, next_n, day, _at(day, rng.randint(0, 600)))
            customers[c["customer_id"]] = c
            delta.append(dict(c))
            next_n += 1

        name = f"customers/customers_{day.isoformat()}.csv"
        columns: tuple[str, ...] = CUSTOMER_COLUMNS
        if offset == cfg.n_days - 1:
            # Cambio de esquema: la fuente añade una columna sin avisar.
            columns = (*CUSTOMER_COLUMNS, "loyalty_tier")
            for row in delta:
                row["loyalty_tier"] = rng.choice(("bronze", "silver", "gold"))
            ds.defects.append(
                Defect(
                    "customers_csv",
                    name,
                    DefectKind.SCHEMA_CHANGE,
                    "*",
                    "loyalty_tier",
                    "columna nueva",
                )
            )
        ds.files[name] = _csv(columns, delta)
        ds.rows[name] = len(delta)
        if offset == 1:
            # Reenvío idéntico con otro nombre: debe detectarse por hash, no por nombre.
            resend = f"customers/customers_{day.isoformat()}_resend.csv"
            ds.files[resend] = ds.files[name]
            ds.rows[resend] = len(delta)
            ds.defects.append(
                Defect(
                    "customers_csv",
                    resend,
                    DefectKind.IDENTICAL_FILE_RESEND,
                    name,
                    None,
                    "mismo contenido",
                )
            )
        available.append(sorted(customers))

    ds.expectations["customers_distinct"] = len(customers)
    ds.expectations["customer_attribute_changes"] = changes_total
    return available


# --------------------------------------------------------------------------- pedidos
def _order(
    rng: random.Random,
    n: int,
    day: date,
    customers: list[str],
    prices: dict[int, float],
) -> dict[str, Any]:
    ts = _at(day, rng.randint(0, 1439))
    items = []
    for line in range(1, rng.randint(1, 4) + 1):
        pid = rng.randint(1, len(prices))
        items.append(
            {
                "line_no": line,
                "product_id": pid,
                "quantity": rng.randint(1, 5),
                "unit_price": prices[pid],
            }
        )
    return {
        "order_id": f"O{n:06d}",
        "customer_id": rng.choice(customers),
        "order_ts": _iso(ts),
        "status": rng.choices(vocab.ORDER_STATUSES, weights=(10, 25, 25, 30, 6, 4))[0],
        "currency": "USD",
        "updated_at": _iso(ts + timedelta(minutes=rng.randint(1, 120))),
        "items": items,
    }


def _orders(
    cfg: GeneratorConfig, ds: Dataset, available: list[list[str]], prices: dict[int, float]
) -> None:
    rng = _rng(cfg.seed, "orders")
    batches: list[list[dict[str, Any]]] = []
    n = 1
    for offset in range(cfg.n_days):
        day = cfg.start + timedelta(days=offset)
        batch = []
        for _ in range(cfg.orders_per_day):
            batch.append(_order(rng, n, day, available[offset], prices))
            n += 1
        batches.append(batch)
    distinct_orders = n - 1

    names = [f"orders/orders_batch_{i + 1:03d}.json" for i in range(cfg.n_days)]
    b1, b2, b3 = batches[0], batches[1], batches[2]
    picks1 = rng.sample(range(len(b1)), 13)

    # Lote 2 reenvía 5 pedidos idénticos del lote 1 (idempotencia por hash de registro).
    for i in picks1[0:5]:
        b2.append(json.loads(json.dumps(b1[i])))
        ds.defects.append(
            Defect(
                "orders_json",
                names[1],
                DefectKind.DUPLICATE_ACROSS_BATCHES,
                b1[i]["order_id"],
                None,
                "pedido repetido",
            )
        )
    # Lote 3 trae versiones nuevas de 5 pedidos del lote 1 (gana la última versión).
    late_day = cfg.start + timedelta(days=2)
    for i in picks1[5:10]:
        late = json.loads(json.dumps(b1[i]))
        late["status"] = "returned" if late["status"] == "delivered" else "cancelled"
        late["updated_at"] = _iso(_at(late_day, rng.randint(0, 1439)))
        b3.append(late)
        ds.defects.append(
            Defect(
                "orders_json",
                names[2],
                DefectKind.LATE_UPDATE,
                late["order_id"],
                "status",
                late["status"],
            )
        )
    # Defectos de fila en el lote 1 (sobre pedidos que no se reenvían).
    for i in picks1[10:12]:
        b1[i]["items"][0]["quantity"] = "two"
        ds.defects.append(
            Defect(
                "orders_json",
                names[0],
                DefectKind.INVALID_TYPE,
                b1[i]["order_id"],
                "items.quantity",
                "'two'",
            )
        )
    b1[picks1[12]]["items"] = []
    ds.defects.append(
        Defect(
            "orders_json",
            names[0],
            DefectKind.EMPTY_COLLECTION,
            b1[picks1[12]]["order_id"],
            "items",
            "sin líneas",
        )
    )

    picks2 = rng.sample(range(cfg.orders_per_day), 4)  # solo pedidos originales del lote 2
    for i in picks2[0:2]:
        b2[i]["customer_id"] = None
        ds.defects.append(
            Defect(
                "orders_json",
                names[1],
                DefectKind.MISSING_REQUIRED,
                b2[i]["order_id"],
                "customer_id",
                "null",
            )
        )
    for i, qty in zip(picks2[2:4], (0, -1), strict=True):
        b2[i]["items"][0]["quantity"] = qty
        ds.defects.append(
            Defect(
                "orders_json",
                names[1],
                DefectKind.OUT_OF_RANGE,
                b2[i]["order_id"],
                "items.quantity",
                str(qty),
            )
        )

    picks3 = rng.sample(range(cfg.orders_per_day), 4)
    for i in picks3[0:2]:
        b3[i]["items"][0]["product_id"] = ORPHAN_PRODUCT_ID
        ds.defects.append(
            Defect(
                "orders_json",
                names[2],
                DefectKind.ORPHAN_REFERENCE,
                b3[i]["order_id"],
                "items.product_id",
                str(ORPHAN_PRODUCT_ID),
            )
        )
    for i in picks3[2:4]:
        b3[i]["customer_id"] = ORPHAN_CUSTOMER_ID
        ds.defects.append(
            Defect(
                "orders_json",
                names[2],
                DefectKind.ORPHAN_REFERENCE,
                b3[i]["order_id"],
                "customer_id",
                ORPHAN_CUSTOMER_ID,
            )
        )

    for offset, (name, batch) in enumerate(zip(names, batches, strict=True)):
        day = cfg.start + timedelta(days=offset)
        payload = {
            "batch_ref": f"orders-{day.isoformat()}",
            "exported_at": _iso(_at(day, 1439)),
            "orders": batch,
        }
        ds.files[name] = _json(payload)
        ds.rows[name] = len(batch)
    ds.expectations["orders_distinct"] = distinct_orders
    ds.expectations["order_records_total"] = sum(len(b) for b in batches)


# --------------------------------------------------------------------------- API pública
def generate(cfg: GeneratorConfig) -> Dataset:
    ds = Dataset()
    prices = _products(cfg, ds)
    available = _customers(cfg, ds)
    _orders(cfg, ds, available, prices)
    ds.defects.sort(key=lambda d: (d.source, d.file, d.kind, d.key))
    return ds


def manifest(cfg: GeneratorConfig, ds: Dataset) -> dict[str, Any]:
    cfg_dict = asdict(cfg)
    cfg_dict["start"] = cfg.start.isoformat()
    return {
        "generator_version": GENERATOR_VERSION,
        "config": cfg_dict,
        "files": [
            {"path": path, "sha256": sha256_text(content), "rows": ds.rows[path]}
            for path, content in sorted(ds.files.items())
        ],
        "expectations": dict(sorted(ds.expectations.items())),
        "defects": [{**asdict(d), "kind": str(d.kind)} for d in ds.defects],
    }


def write(cfg: GeneratorConfig, out_dir: Path) -> dict[str, Any]:
    ds = generate(cfg)
    for path, content in ds.files.items():
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" evita que Windows convierta \n en \r\n (cambiaría el hash).
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    meta = manifest(cfg, ds)
    with (out_dir / "manifest.json").open("w", encoding="utf-8", newline="") as fh:
        fh.write(_json(meta))
    return meta


def check(cfg: GeneratorConfig, out_dir: Path) -> list[str]:
    """Compara los archivos existentes con lo que generaría ``cfg``. Devuelve diferencias."""
    expected = manifest(cfg, generate(cfg))
    problems = []
    for entry in expected["files"]:
        target = out_dir / entry["path"]
        if not target.exists():
            problems.append(f"falta {entry['path']}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            problems.append(f"difiere {entry['path']}")
    return problems
