# run_scripts/run_tick_rolling_breakout_gold.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.tick_rolling_breakout import TickRollingBreakoutStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2026-05-01 09:00:00"
END_DATE = "2026-05-15 15:00:00"

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "enabled_entry_symbols": ["ag"],
    "breakout_window_seconds": 360.0,
    "breakout_mode": "fade",
    "confirm_window_seconds": 45.0,
    "min_range_ticks": 32.0,
    "breakout_ticks": 1.0,
    "min_directional_ratio": 0.72,
    "min_ticks_in_window": 120,
    "min_tick_volume": 0.0,
    "use_imbalance_filter": False,
    "imbalance_threshold": 0.10,
    "max_spread_ticks": 2.0,
    "hold_seconds": 45.0,
    "take_profit_ticks": 22.0,
    "stop_loss_ticks": 14.0,
    "cooldown_seconds": 180.0,
    "avoid_session_close_seconds": 180.0,
    "max_entries_per_symbol_per_day": 10,
    "allowed_entry_hours": [21, 22, 23, 0, 1, 2],
    "exit_order_type": "opponent",
    "exit_order_ttl_seconds": 2.0,
    "sizing": {
        "mode": "fixed_volume",
        "value": 1,
        "min_volume": 1,
        "max_volume": 1,
        "round_lot": 1,
    },
    "execution": {
        "order_type": "opponent",
        "price_field": "last_price",
        "slippage_ticks": 0.0,
        "order_ttl_seconds": 2.0,
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
        strategy_class=TickRollingBreakoutStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq="tick",
        data_type="main",
        initial_capital=1_000_000.0,
        strategy_kwargs=STRATEGY_KWARGS,
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
