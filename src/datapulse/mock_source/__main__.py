"""Arranca la API simulada: ``python -m datapulse.mock_source``."""

from __future__ import annotations

import os

import uvicorn

from datapulse.mock_source.app import create_app


def main() -> None:
    host = os.environ.get("DATAPULSE_MOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("DATAPULSE_MOCK_PORT", "8081"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
