# run_scripts/run_amplitude_rank_day_breakout.py
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
from strategy.custom.amplitude_rank_acd import (
    AmplitudeRankDayBreakoutStrategy,
    build_amplitude_rank_selector,
)


TARGET_SYMBOLS = [code.lower() for code in SYMBOL_DICT if code.lower() in FEE_DICT]
QUERY_SYMBOLS = [build_query_symbol(sym, "main") for sym in TARGET_SYMBOLS]
QUERY_SYMBOLS = [sym for sym in QUERY_SYMBOLS if sym]

DEFAULT_START_DATE = "2025-05-15 09:00:00"
DEFAULT_END_DATE = "2026-05-15 15:00:00"
SELECTOR_TRAIN_START = "2018-01-01"


def parse_symbols(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def build_selector(args):
    provider = DataProvider()
    daily_df = provider.get_history(
        symbols=QUERY_SYMBOLS,
        start_date=SELECTOR_TRAIN_START,
        end_date=args.end_date,
        freq="1d",
        data_type="main",
    )
    selector, selection_table = build_amplitude_rank_selector(
        daily_wide_df=daily_df,
        backtest_start=args.start_date,
        backtest_end=args.end_date,
        top_n=args.top_n,
        weight_method=args.weight_method,
        max_per_sector=args.max_per_sector or None,
        min_daily_turnover=args.min_daily_turnover,
        max_round_trip_cost_bps=args.max_cost_bps,
        exclude_symbols=parse_symbols(args.exclude_symbols),
        train_start=SELECTOR_TRAIN_START,
    )

    output_dir = PROJECT_ROOT / "exports" / "selectors"
    output_dir.mkdir(parents=True, exist_ok=True)
    sector_tag = f"sector{args.max_per_sector}" if args.max_per_sector else "nosectorcap"
    cost_tag = f"cost{args.max_cost_bps:g}" if args.max_cost_bps is not None else "nocostcap"
    path = output_dir / (
        f"amplitude_rank_day_breakout_top{args.top_n}_{args.weight_method}_"
        f"{sector_tag}_{cost_tag}_selection.csv"
    )
    selection_table.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[Selector] Selection table exported: {path}")
    return selector


def build_strategy_kwargs(selector_by_date: dict, args) -> dict:
    return {
        "target_symbols": TARGET_SYMBOLS,
        "selector_by_date": selector_by_date,
        "opening_range_bars": args.opening_range_bars,
        "confirm_bars": args.confirm_bars,
        "flatten_time": args.flatten_time,
        "atr_period": 48,
        "trend_window": 96,
        "breakout_buffer_ticks": args.breakout_buffer_ticks,
        "min_range_atr": args.min_range_atr,
        "max_extension_atr": args.max_extension_atr,
        "atr_stop_mult": args.atr_stop_mult,
        "trail_atr_mult": args.trail_atr_mult,
        "take_profit_atr": args.take_profit_atr,
        "exit_on_range_reentry": args.exit_on_range_reentry,
        "max_hold_bars": args.max_hold_bars,
        "cooldown_bars": args.cooldown_bars,
        "max_entries_per_symbol_per_day": args.max_entries_per_symbol_per_day,
        "session_start_hours": "9,21",
        "allowed_entry_hours": "9,10,13,21,22,23,0,1",
        "sizing": {
            "mode": "equity_pct",
            "value": args.margin_target,
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
    parser = argparse.ArgumentParser(description="Run LightGBM amplitude-rank intraday breakout strategy.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--margin-target", type=float, default=0.25)
    parser.add_argument("--weight-method", choices=["score", "equal"], default="score")
    parser.add_argument("--max-per-sector", type=int, default=1)
    parser.add_argument("--min-daily-turnover", type=float, default=1e9)
    parser.add_argument("--max-cost-bps", type=float, default=3.0)
    parser.add_argument("--exclude-symbols", default="")
    parser.add_argument("--opening-range-bars", type=int, default=6)
    parser.add_argument("--confirm-bars", type=int, default=2)
    parser.add_argument("--breakout-buffer-ticks", type=float, default=1.0)
    parser.add_argument("--min-range-atr", type=float, default=0.35)
    parser.add_argument("--max-extension-atr", type=float, default=1.8)
    parser.add_argument("--atr-stop-mult", type=float, default=2.0)
    parser.add_argument("--trail-atr-mult", type=float, default=2.8)
    parser.add_argument("--take-profit-atr", type=float, default=0.0)
    parser.add_argument("--max-hold-bars", type=int, default=96)
    parser.add_argument("--cooldown-bars", type=int, default=24)
    parser.add_argument("--max-entries-per-symbol-per-day", type=int, default=1)
    parser.add_argument("--flatten-time", default="14:45")
    parser.add_argument("--exit-on-range-reentry", action="store_true")
    args = parser.parse_args()

    selector = build_selector(args)
    analyzer = run_backtest(
        strategy_class=AmplitudeRankDayBreakoutStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="5m",
        data_type="main",
        initial_capital=1_000_000.0,
        strategy_kwargs=build_strategy_kwargs(selector, args),
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
