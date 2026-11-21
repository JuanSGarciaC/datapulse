"""Vista de la cuarentena para la API, sin datos personales.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21

La API (dp_reader) no puede leer ``raw`` (ADR-0005). Esta vista vive en ``ops`` y la
ejecuta su dueño, así que expone solo lo necesario: cada fila apartada con su lote, su
archivo y sus errores, sin nombres y con el email enmascarado. dp_pipeline hereda por
defecto INSERT/UPDATE en ``ops``: se le retiran para que la vista no sea una puerta a
``raw``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MASKED_EMAIL = """
    CASE WHEN q.payload->>'email' IS NULL THEN NULL
         ELSE left(q.payload->>'email', 2) || '***'
              || coalesce(substring(q.payload->>'email' FROM '@.*$'), '')
    END
"""


def upgrade() -> None:
    op.execute(f"""
        CREATE VIEW ops.v_quarantine AS
        SELECT q._raw_id AS quarantine_id,
               q.source_name,
               q._batch_id AS batch_id,
               r.source_ref,
               r.content_sha256,
               r.run_id,
               q._row_number AS row_number,
               q.record_key,
               q.errors,
               CASE WHEN jsonb_exists(q.payload, 'email')
                    THEN (q.payload - 'first_name' - 'last_name' - 'email')
                         || jsonb_build_object('email', {_MASKED_EMAIL})
                    ELSE q.payload - 'first_name' - 'last_name'
               END AS payload_masked,
               q._quarantined_at AS quarantined_at
        FROM raw.quarantine q
        JOIN ops.ingestion_registry r ON r.batch_id = q._batch_id
    """)
    op.execute("REVOKE ALL ON ops.v_quarantine FROM dp_pipeline, dp_transform")
    op.execute("GRANT SELECT ON ops.v_quarantine TO dp_reader")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ops.v_quarantine")
