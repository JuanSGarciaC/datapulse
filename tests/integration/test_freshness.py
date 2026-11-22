"""Frescura por fuente contra PostgreSQL real.

Va en su propio módulo porque usa ``migrated_db``, que reconstruye la base de pruebas: dentro
de test_api.py borraría el almacén que comparten las pruebas de ese módulo.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from datapulse.api import repository as repo
from datapulse.observability.runs import RunRecorder

pytestmark = pytest.mark.integration


def test_freshness_counts_the_last_good_load_not_the_last_attempt(migrated_db: Engine) -> None:
    with RunRecorder(migrated_db, "ingest_orders", source_name="orders_json"):
        pass  # carga buena
    with (
        pytest.raises(RuntimeError),
        RunRecorder(migrated_db, "ingest_orders", source_name="orders_json"),
    ):
        raise RuntimeError("la fuente no respondió")
    with RunRecorder(migrated_db, "transform"):
        pass  # sin fuente: no cuenta
    with migrated_db.connect() as conn:
        rows = repo.freshness(conn)
        good = conn.execute(
            text(
                "SELECT max(finished_at) FROM ops.pipeline_runs "
                "WHERE source_name = 'orders_json' AND status = 'success'"
            )
        ).scalar_one()
    assert [r["source_name"] for r in rows] == ["orders_json"]
    assert rows[0]["last_status"] == "failed"  # el último intento falló…
    assert rows[0]["last_success_at"] == good  # …pero la última carga buena sigue contando
