"""``python -m datapulse.api`` → uvicorn con la fábrica de la app."""

from __future__ import annotations

import os

import uvicorn

from datapulse.config import get_settings
from datapulse.observability.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        "datapulse.api.app:create_app",
        factory=True,
        host=os.environ.get("DATAPULSE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("DATAPULSE_API_PORT", "8000")),
        proxy_headers=False,
        server_header=False,
        log_config=None,  # usa el logging JSON ya configurado
    )


if __name__ == "__main__":  # pragma: no cover
    main()
