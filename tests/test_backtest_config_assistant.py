# -*- coding: utf-8 -*-
from datetime import date
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.app import _symbol_button_label, _symbol_selection_label, build_backtest_config_draft


AVAILABLE_KEYS = {
    "dual_ma",
    "general_multi_ma",
    "zscore_reversal",
    "donchian_atr_breakout",
}


def test_config_assistant_builds_conservative_gold_zscore_daily_draft():
    result = build_backtest_config_draft(
        "我想测黄金日线，2024 到现在，ZScore 反转策略，保守一点",
        {},
        AVAILABLE_KEYS,
        today=date(2026, 6, 23),
    )

    config = result.config

    assert config["strategy"] == "zscore_reversal"
    assert config["symbols"] == ["au"]
    assert config["freq"] == "1d"
    assert config["data_type"] == "main"
    assert config["start_date"] == "2024-01-01"
    assert config["end_date"] == "2026-06-23"
    assert config["sizing_value"] == 0.02
    assert config["max_volume"] == 1
    assert config["entry_z"] == 2.6


def test_config_assistant_uses_all_data_for_specific_contract():
    result = build_backtest_config_draft(
        "测试 au2606 单合约日线，从 2025-01-01 到 2026-06-22",
        {"strategy": "general_multi_ma"},
        AVAILABLE_KEYS,
        today=date(2026, 6, 23),
    )

    config = result.config

    assert config["strategy"] == "general_multi_ma"
    assert config["symbols"] == ["au2606"]
    assert config["freq"] == "1d"
    assert config["data_type"] == "all"
    assert config["start_date"] == "2025-01-01"
    assert config["end_date"] == "2026-06-22"


def test_config_assistant_warns_when_symbols_are_inherited():
    result = build_backtest_config_draft(
        "只改成 ZScore 日线",
        {"symbols": ["rb", "au"], "strategy": "general_multi_ma"},
        AVAILABLE_KEYS,
        today=date(2026, 6, 23),
    )

    assert result.config["symbols"] == ["rb", "au"]
    assert any("沿用当前表单" in item for item in result.summary)
    assert any("没有从描述中识别到新的品种" in item for item in result.warnings)


def test_symbol_button_label_includes_chinese_name_for_gold():
    assert _symbol_button_label("au") == "黄金 au"
    assert _symbol_selection_label("au") == "黄金 au"
