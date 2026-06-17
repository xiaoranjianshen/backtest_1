# run_scripts/run_utbot_stc_hull_gold.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.utbot_stc_hull import UTBotSTCHullStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2025-05-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "hma_length": 55,
    "atr_period": 10,
    "ut_key_value": 1.0,
    "stc_length": 12,
    "stc_fast": 26,
    "stc_slow": 50,
    "stc_factor": 0.5,
    "stc_long_max": 35.0,
    "stc_short_min": 65.0,
    "require_price_above_hull": True,
    "exit_on_opposite_signal": True,
    "take_profit_ticks": 24.0,
    "stop_loss_ticks": 16.0,
    "max_hold_bars": 36,
    "cooldown_bars": 3,
    "max_entries_per_symbol_per_day": 20,
    "sizing": {
        "mode": "fixed_volume",
        "value": 1,
        "min_volume": 1,
        "max_volume": 1,
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
        strategy_class=UTBotSTCHullStrategy,
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
