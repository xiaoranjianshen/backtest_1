# run_scripts/run_trend_rank_donchian.py
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from config import FEE_DICT, SYMBOL_DICT, build_query_symbol
from data_feed.data_provider import DataProvider
from frontend_index import build_html_dashboard
from strategy.custom.trend_rank_donchian import (
    TrendRankDonchianStrategy,
    build_trend_rank_selector,
)


TARGET_SYMBOLS = [code.lower() for code in SYMBOL_DICT if code.lower() in FEE_DICT]
QUERY_SYMBOLS = [build_query_symbol(sym, "main") for sym in TARGET_SYMBOLS]
QUERY_SYMBOLS = [sym for sym in QUERY_SYMBOLS if sym]

DEFAULT_START_DATE = "2025-05-15 09:00:00"
DEFAULT_END_DATE = "2026-05-15 15:00:00"
SELECTOR_START_DATE = "2018-01-01"


def build_selector(args):
    provider = DataProvider()
    daily_df = provider.get_history(
        symbols=QUERY_SYMBOLS,
        start_date=SELECTOR_START_DATE,
        end_date=args.end_date,
        freq="1d",
        data_type="main",
    )
    selector, selection_table = build_trend_rank_selector(
        daily_wide_df=daily_df,
        backtest_start=args.start_date,
        backtest_end=args.end_date,
        top_n=args.top_n,
        weight_method=args.weight_method,
        max_per_sector=args.max_per_sector or None,
        rebalance_days=args.rebalance_days,
        min_daily_turnover=args.min_daily_turnover,
        min_abs_ret_10d=args.min_abs_ret_10d,
        min_efficiency_10d=args.min_efficiency_10d,
        max_cost_bps=args.max_cost_bps,
    )

    output_dir = PROJECT_ROOT / "exports" / "selectors"
    output_dir.mkdir(parents=True, exist_ok=True)
    sector_tag = f"sector{args.max_per_sector}" if args.max_per_sector else "nosectorcap"
    path = output_dir / (
        f"trend_rank_donchian_top{args.top_n}_{args.weight_method}_"
        f"rebalance{args.rebalance_days}_{sector_tag}_selection.csv"
    )
    selection_table.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[Selector] Selection table exported: {path}")
    return selector


def build_strategy_kwargs(selector_by_date: dict, margin_target: float) -> dict:
    return {
        "target_symbols": TARGET_SYMBOLS,
        "selector_by_date": selector_by_date,
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
        "max_entries_per_symbol_per_day": 2,
        "allowed_entry_hours": "9,10,13,21,22,23,0,1,2",
        "sizing": {
            "mode": "equity_pct",
            "value": margin_target,
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
        "record_signals": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run trend-rank Donchian/ATR strategy.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--margin-target", type=float, default=0.25)
    parser.add_argument("--weight-method", choices=["score", "equal"], default="score")
    parser.add_argument("--max-per-sector", type=int, default=1)
    parser.add_argument("--rebalance-days", type=int, default=5)
    parser.add_argument("--min-daily-turnover", type=float, default=1e9)
    parser.add_argument("--min-abs-ret-10d", type=float, default=0.015)
    parser.add_argument("--min-efficiency-10d", type=float, default=0.18)
    parser.add_argument("--max-cost-bps", type=float, default=12.0)
    args = parser.parse_args()

    selector = build_selector(args)
    analyzer = run_backtest(
        strategy_class=TrendRankDonchianStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="5m",
        data_type="main",
        initial_capital=1_000_000.0,
        strategy_kwargs=build_strategy_kwargs(selector, margin_target=args.margin_target),
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
