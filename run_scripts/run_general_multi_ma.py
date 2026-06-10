# -*- coding: utf-8 -*-
"""
Recommended runnable entry: GeneralMultiMA.

This runner opens the HTML report after the backtest finishes.
"""
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.general_multi_ma import GeneralMultiMAStrategy


TARGET_SYMBOLS = ["rb", "hc", "i", "ta", "ma", "p", "y", "sr"]

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "fast_window": 10,
    "slow_window": 30,
    "sizing": {
        "mode": "equity_pct",
        "value": 0.03,
        "min_volume": 1,
        "max_volume": None,
    },
    "execution": {
        "order_type": "market",
        "slippage_ticks": 0.5,
    },
    "exit": {
        "close_pct": 1.0,
        "allow_reverse": True,
        "respect_pending_orders": True,
    },
    "record_signals": True,
}


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=GeneralMultiMAStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date="2021-01-01 00:00:00",
        end_date="2022-01-01 23:59:59",
        freq="1d",
        data_type="main",
        initial_capital=5_000_000.0,
        strategy_kwargs=STRATEGY_KWARGS,
    )
    build_html_dashboard(analyzer)
