# run_scripts/run_opening_range_acd_pool.py
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy


def parse_symbols(value: str) -> list[str]:
    return [item.strip().lower() for item in value.replace(";", ",").split(",") if item.strip()]


def build_strategy_kwargs(symbols: list[str], equity_pct: float) -> dict:
    return {
        "target_symbols": symbols,
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
            "value": float(equity_pct),
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
    parser = argparse.ArgumentParser(description="Run Opening Range ACD on a custom symbol pool.")
    parser.add_argument("--symbols", default="au,ag")
    parser.add_argument("--start-date", default="2025-05-15 09:00:00")
    parser.add_argument("--end-date", default="2026-05-15 15:00:00")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--equity-pct", type=float, default=0.10)
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    analyzer = run_backtest(
        strategy_class=OpeningRangeACDStrategy,
        symbols_input=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="5m",
        data_type="main",
        initial_capital=args.initial_capital,
        strategy_kwargs=build_strategy_kwargs(symbols, args.equity_pct),
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
