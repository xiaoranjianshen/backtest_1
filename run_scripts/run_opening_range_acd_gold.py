# run_scripts/run_opening_range_acd_gold.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2025-05-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "opening_range_bars": 3,
    "atr_period": 48,
    "trend_window": 144,
    "breakout_buffer_ticks": 1.0,
    "min_range_atr": 0.5,
    "max_extension_atr": 1.2,
    "atr_stop_mult": 3.0,
    "trail_atr_mult": 4.0,
    "take_profit_atr": 0.0,
    "exit_on_range_reentry": False,
    "max_hold_bars": 384,
    "cooldown_bars": 18,
    "max_entries_per_symbol_per_day": 2,
    "session_start_hours": "9,21",
    "allowed_entry_hours": "9,10,21,22,23,0,1",
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
        strategy_class=OpeningRangeACDStrategy,
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
