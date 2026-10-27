"""Tablas de la capa raw (clientes, pedidos, productos) y cuarentena.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27

Los permisos llegan solos: la migración 0001 definió privilegios por defecto en
``raw`` (dp_pipeline: SELECT/INSERT; dp_transform: SELECT). Una prueba de
integración verifica que estas tablas los heredaron.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _technical(table: str) -> list[sa.Column[object] | sa.Constraint]:
    return [
        sa.Column("_raw_id", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("_batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("_source_ref", sa.Text, nullable=False),
        sa.Column("_row_number", sa.Integer),
        sa.Column("_record_hash", sa.String(64), nullable=False),
        sa.Column("_extra", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "_ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("_raw_id", name=f"pk_{table}"),
        sa.ForeignKeyConstraint(
            ["_batch_id"],
            ["ops.ingestion_registry.batch_id"],
            name=f"fk_{table}__batch_id_ingestion_registry",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("_record_hash", name=f"uq_{table}__record_hash"),
    ]


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.String(20), nullable=False),
        sa.Column("first_name", sa.Text),
        sa.Column("last_name", sa.Text),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("city", sa.Text),
        sa.Column("country", sa.String(2)),
        sa.Column("segment", sa.String(20)),
        sa.Column("signup_date", sa.Date),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_technical("customers"),
        schema="raw",
    )
    op.create_index(
        "ix_raw_customers_key_updated", "customers", ["customer_id", "updated_at"], schema="raw"
    )

    op.create_table(
        "orders",
        sa.Column("order_id", sa.String(20), nullable=False),
        sa.Column("customer_id", sa.String(20), nullable=False),
        sa.Column("order_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("items", pg.JSONB, nullable=False),
        *_technical("orders"),
        schema="raw",
    )
    op.create_index("ix_raw_orders_key_updated", "orders", ["order_id", "updated_at"], schema="raw")

    op.create_table(
        "products",
        sa.Column("product_id", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("brand", sa.Text),
        sa.Column("sku", sa.Text),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_percentage", sa.Numeric(5, 2)),
        sa.Column("rating", sa.Numeric(3, 2)),
        sa.Column("stock", sa.Integer),
        sa.Column("availability_status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_technical("products"),
        schema="raw",
    )
    op.create_index(
        "ix_raw_products_key_updated", "products", ["product_id", "updated_at"], schema="raw"
    )

    op.create_table(
        "quarantine",
        sa.Column("_raw_id", sa.BigInteger, sa.Identity(always=False), nullable=False),
        sa.Column("_batch_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("_source_ref", sa.Text, nullable=False),
        sa.Column("_row_number", sa.Integer),
        sa.Column("record_key", sa.Text),
        sa.Column("_record_hash", sa.String(64), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("errors", pg.JSONB, nullable=False),
        sa.Column(
            "_quarantined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("_raw_id", name="pk_quarantine"),
        sa.ForeignKeyConstraint(
            ["_batch_id"],
            ["ops.ingestion_registry.batch_id"],
            name="fk_quarantine__batch_id_ingestion_registry",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_name", "_record_hash", name="uq_quarantine_source_name__record_hash"
        ),
        schema="raw",
    )


def downgrade() -> None:
    # CASCADE: las vistas de staging (dbt) dependen de estas tablas. dbt las recrea en su
    # siguiente ejecución; sin CASCADE el downgrade fallaría en cuanto dbt haya corrido.
    for table in ("quarantine", "products", "orders", "customers"):
        op.execute(f"DROP TABLE IF EXISTS raw.{table} CASCADE")
