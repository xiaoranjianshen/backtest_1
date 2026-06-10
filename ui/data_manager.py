# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ui_config import PROJECT_ROOT, RUNTIME_DIR


class BacktestDataManager:
    def __init__(self):
        self.runtime_dir = RUNTIME_DIR
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def write_config(self, config: dict) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.runtime_dir / f"run_config_{stamp}.json"
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def project_python(self) -> str:
        configured = os.environ.get("BACKTEST_PYTHON")
        if configured:
            return configured
        return sys.executable

    def config_command(self, config_path: Path) -> list[str]:
        return [
            self.project_python(),
            str(PROJECT_ROOT / "ui" / "run_from_config.py"),
            str(config_path),
        ]
