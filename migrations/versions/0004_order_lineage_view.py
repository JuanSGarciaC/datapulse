"""Vista de linaje de pedidos: cada versión recibida con su lote, archivo y ejecución.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21

La API no lee ``raw`` (ADR-0005). Esta vista en ``ops`` expone de ``raw.orders`` solo lo
que el linaje necesita (sin líneas de pedido ni ``_extra``), y marca la versión vigente con
el mismo orden que ``stg_orders``: así el panel muestra exactamente la versión que dbt eligió.
Mismo trato de permisos que ``ops.v_quarantine`` (0003).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE VIEW ops.v_order_lineage AS
        SELECT o.order_id,
               o.customer_id,
               o.status,
               o.order_ts,
               o.updated_at,
               o._ingested_at AS ingested_at,
               jsonb_array_length(o.items) AS item_count,
               row_number() OVER (
                   PARTITION BY o.order_id
                   ORDER BY o.updated_at DESC, o._ingested_at DESC, o._raw_id DESC
               ) AS version_rank,
               count(*) OVER (PARTITION BY o.order_id) AS versions_received,
               o._batch_id AS batch_id,
               o._source_ref AS source_ref,
               o._row_number AS row_number,
               r.content_sha256,
               r.run_id,
               p.flow_name,
               p.trigger,
               p.started_at AS run_started_at
        FROM raw.orders o
        JOIN ops.ingestion_registry r ON r.batch_id = o._batch_id
        LEFT JOIN ops.pipeline_runs p ON p.run_id = r.run_id
    """)
    op.execute("REVOKE ALL ON ops.v_order_lineage FROM dp_pipeline, dp_transform")
    op.execute("GRANT SELECT ON ops.v_order_lineage TO dp_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ops.v_order_lineage")
