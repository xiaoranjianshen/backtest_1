# run_scripts/run_donchian_atr_breakout_gold.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.donchian_atr_breakout import DonchianATRBreakoutStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2025-05-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "donchian_window": 144,
    "atr_period": 72,
    "trend_window": 240,
    "breakout_buffer_ticks": 2.0,
    "min_channel_atr": 1.8,
    "max_extension_atr": 1.5,
    "atr_stop_mult": 3.2,
    "exit_on_midline": True,
    "max_hold_bars": 384,
    "cooldown_bars": 18,
    "max_entries_per_symbol_per_day": 3,
    "allowed_entry_hours": "9,10,13,21,22,23,0,1,2",
    "sizing": {
        "mode": "equity_pct",
        "value": 0.10,
        "min_volume": 1,
        "max_volume": None,
        "round_lot": 1,
    },
    "execution": {
        "order_type": "market",
        "price_field": "close",
        "slippage_ticks": 0.5,
    },
    "exit": {
        "close_pct": 1.0,
        "allow_reverse": False,
        "respect_pending_orders": True,
    },
    "record_signals": True,
}


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=DonchianATRBreakoutStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq="5m",
        data_type="main",
        initial_capital=1_000_000.0,
        strategy_kwargs=STRATEGY_KWARGS,
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
