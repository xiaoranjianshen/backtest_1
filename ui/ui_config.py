# -*- coding: utf-8 -*-
from pathlib import Path


APP_TITLE = "Backtest Configuration"
APP_ICON = None
LAYOUT = "wide"

UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
ANALYZER_DIR = PROJECT_ROOT / "analyzer"
RUNTIME_DIR = UI_DIR / ".runtime"
ACTIVE_REPORT_CONFIG_PATH = RUNTIME_DIR / "active_report_config.json"


CUSTOM_CSS = """
<style>
    .block-container {
        padding-top: 1.25rem;
        padding-bottom: 1.5rem;
        max-width: 1440px;
    }
    .main-header {
        border-bottom: 1px solid #e5e7eb;
        padding-bottom: 0.75rem;
        margin-bottom: 1rem;
    }
    .main-title {
        font-size: 1.35rem;
        font-weight: 700;
        color: #111827;
        margin: 0;
    }
    .main-caption {
        font-size: 0.86rem;
        color: #6b7280;
        margin-top: 0.2rem;
    }
    .status-note {
        border: 1px solid #d1d5db;
        border-radius: 8px;
        padding: 0.75rem 0.9rem;
        background: #f9fafb;
        color: #374151;
        font-size: 0.88rem;
    }
    .small-code {
        font-family: Consolas, Menlo, monospace;
        font-size: 0.8rem;
        color: #374151;
    }
</style>
"""
