# -*- coding: utf-8 -*-
"""
Runnable entry: Composite factor strategy.

This runner opens the HTML report after the backtest finishes.
"""
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.factor_template.composite_factor import CompositeFactorStrategy


TARGET_SYMBOLS = ["rb", "hc", "i", "jm", "j", "ta", "ma", "fg", "sa"]


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=CompositeFactorStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date="2020-05-20 00:00:00",
        end_date="2026-05-20 23:59:59",
        freq="1d",
        data_type="main",
        initial_capital=5_000_000.0,
        strategy_kwargs={
            "target_symbols": TARGET_SYMBOLS,
            "rebalance_period": 5,
            "top_k": 2,
            "signal_scale": 1.0,
            "sizing": {"mode": "equity_pct", "value": 0.03, "min_volume": 1},
            "execution": {"order_type": "market", "slippage_ticks": 0.5},
            "exit": {"close_pct": 1.0, "allow_reverse": True, "respect_pending_orders": True},
            "record_signals": True,
        },
    )
    build_html_dashboard(analyzer)
