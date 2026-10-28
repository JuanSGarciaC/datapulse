"""``python -m datapulse.dashboard`` → ``streamlit run`` sobre la página del dashboard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from streamlit.web import cli as stcli


def main() -> None:
    page = Path(__file__).with_name("app.py")
    sys.argv = [
        "streamlit",
        "run",
        str(page),
        "--server.address",
        os.environ.get("DATAPULSE_DASHBOARD_HOST", "127.0.0.1"),
        "--server.port",
        os.environ.get("DATAPULSE_DASHBOARD_PORT", "8501"),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.enableXsrfProtection",
        "true",
        "--theme.base",
        "light",
        "--client.toolbarMode",
        "minimal",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":  # pragma: no cover
    main()
