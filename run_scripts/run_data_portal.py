# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "data_portal" / "app.py"


def main() -> None:
    host = os.getenv("DATA_PORTAL_HOST", "0.0.0.0")
    port = os.getenv("DATA_PORTAL_PORT", "8601")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(APP_PATH),
            "--server.address",
            host,
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(PROJECT_ROOT),
        check=False,
    )


if __name__ == "__main__":
    main()
