# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from config import FEE_DICT, SYMBOL_DICT, build_query_symbol, pure_product_code
from frontend_index import build_html_dashboard
from strategy.custom.volatility_regime_switch import DailyVolatilityRegimeSwitchStrategy


DEFAULT_START_DATE = "2023-06-01 00:00:00"
DEFAULT_END_DATE = "2026-06-01 23:59:59"


def all_main_symbols() -> list[str]:
    symbols = []
    for code in SYMBOL_DICT:
        raw = pure_product_code(code)
        if raw not in FEE_DICT and raw.upper() not in FEE_DICT and raw.lower() not in FEE_DICT:
            continue
        if build_query_symbol(raw, "main") is None:
            continue
        if raw not in symbols:
            symbols.append(raw)
    return symbols


def parse_symbols(value: str | None) -> list[str]:
    if not value:
        return all_main_symbols()
    symbols = []
    for item in value.replace(";", ",").split(","):
        raw = pure_product_code(item.strip())
        if raw and raw not in symbols:
            symbols.append(raw)
    return symbols


def build_strategy_kwargs(args: argparse.Namespace, symbols: list[str]) -> dict:
    return {
        "target_symbols": symbols,
        "vol_window_days": args.vol_window_days,
        "vol_percentile_lookback_days": args.vol_percentile_lookback_days,
        "min_vol_percentile_samples": args.min_vol_percentile_samples,
        "regime_threshold": args.regime_threshold,
        # 0 means no top-N cap in DailyVolatilityRegimeSwitchStrategy.
        "selection_count": 0,
        "rebalance_frequency": args.rebalance_frequency,
        "total_margin_target": args.total_margin_target,
        "confidence_weight": args.confidence_weight,
        "min_selected_confidence": args.min_selected_confidence,
        "min_avg_daily_notional": args.min_avg_daily_notional,
        "min_daily_volatility": args.min_daily_volatility,
        "max_symbol_notional_pct": args.max_symbol_notional_pct,
        "max_symbol_margin_pct": args.max_symbol_margin_pct,
        "excluded_symbols": args.excluded_symbols,
        "use_performance_selection": False,
        "performance_lookback_days": args.performance_lookback_days,
        "performance_weight": 0.0,
        "performance_min_trades": args.performance_min_trades,
        "performance_min_score": None,
        "exploration_count": 0,
        "reversion_model": args.reversion_model,
        "trend_model": args.trend_model,
        "reversion_lookback": args.reversion_lookback,
        "reversion_entry_z": args.reversion_entry_z,
        "reversion_exit_z": args.reversion_exit_z,
        "reversion_rsi_low": args.reversion_rsi_low,
        "reversion_rsi_high": args.reversion_rsi_high,
        "reversion_atr_mult": args.reversion_atr_mult,
        "reversion_max_hold_bars": args.reversion_max_hold_bars,
        "trend_fast_window": args.trend_fast_window,
        "trend_slow_window": args.trend_slow_window,
        "trend_donchian_window": args.trend_donchian_window,
        "trend_atr_period": args.trend_atr_period,
        "trend_atr_mult": args.trend_atr_mult,
        "trend_exit_on_midline": not args.no_trend_midline_exit,
        "max_entries_per_symbol_per_day": args.max_entries_per_symbol_per_day,
        "sizing": {
            "mode": "equity_pct",
            "value": 0.01,
            "min_volume": 0,
            "max_volume": None,
            "round_lot": 1,
        },
        "execution": {
            "order_type": "market",
            "price_field": "close",
            "slippage_ticks": args.slippage_ticks,
        },
        "exit": {
            "close_pct": 1.0,
            "allow_reverse": False,
            "respect_pending_orders": True,
        },
        "record_signals": not args.no_record_signals,
        "record_signal_holds": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily volatility-regime switch strategy.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--symbols", default=None, help="Comma separated product codes. Empty means all tradable products.")
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--initial-capital", type=float, default=5_000_000.0)

    parser.add_argument("--vol-window-days", type=int, default=20)
    parser.add_argument("--vol-percentile-lookback-days", type=int, default=252)
    parser.add_argument("--min-vol-percentile-samples", type=int, default=120)
    parser.add_argument("--regime-threshold", type=float, default=0.80)
    parser.add_argument("--rebalance-frequency", choices=["daily", "weekly", "monthly"], default="weekly")
    parser.add_argument("--total-margin-target", type=float, default=0.30)
    parser.add_argument("--confidence-weight", type=float, default=0.35)
    parser.add_argument("--min-selected-confidence", type=float, default=0.0)
    parser.add_argument("--min-avg-daily-notional", type=float, default=0.0)
    parser.add_argument("--min-daily-volatility", type=float, default=0.0)
    parser.add_argument("--max-symbol-notional-pct", type=float, default=2.0)
    parser.add_argument("--max-symbol-margin-pct", type=float, default=0.0)
    parser.add_argument("--excluded-symbols", default=None)
    parser.add_argument("--performance-lookback-days", type=int, default=22)
    parser.add_argument("--performance-min-trades", type=int, default=3)

    parser.add_argument("--reversion-model", choices=["zscore", "bollinger", "rsi", "donchian_fade", "atr_fade"], default="zscore")
    parser.add_argument("--trend-model", choices=["ma_cross", "ema_cross", "donchian", "atr_breakout"], default="donchian")
    parser.add_argument("--reversion-lookback", type=int, default=48)
    parser.add_argument("--reversion-entry-z", type=float, default=1.8)
    parser.add_argument("--reversion-exit-z", type=float, default=0.15)
    parser.add_argument("--reversion-rsi-low", type=float, default=28.0)
    parser.add_argument("--reversion-rsi-high", type=float, default=72.0)
    parser.add_argument("--reversion-atr-mult", type=float, default=1.8)
    parser.add_argument("--reversion-max-hold-bars", type=int, default=12)
    parser.add_argument("--trend-fast-window", type=int, default=12)
    parser.add_argument("--trend-slow-window", type=int, default=48)
    parser.add_argument("--trend-donchian-window", type=int, default=36)
    parser.add_argument("--trend-atr-period", type=int, default=24)
    parser.add_argument("--trend-atr-mult", type=float, default=1.2)
    parser.add_argument("--no-trend-midline-exit", action="store_true")
    parser.add_argument("--max-entries-per-symbol-per-day", type=int, default=1)

    parser.add_argument("--slippage-ticks", type=float, default=0.5)
    parser.add_argument("--no-record-signals", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-config-ui", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = parse_symbols(args.symbols)
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    print(f"[Daily VolRegime Run] symbols={len(symbols)} | {symbols[:12]}{'...' if len(symbols) > 12 else ''}")

    analyzer = run_backtest(
        strategy_class=DailyVolatilityRegimeSwitchStrategy,
        symbols_input=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="1d",
        data_type="main",
        initial_capital=args.initial_capital,
        strategy_kwargs=build_strategy_kwargs(args, symbols),
        enable_main_rollover=True,
    )

    if analyzer is not None:
        build_html_dashboard(
            analyzer,
            open_browser=not args.no_browser,
            start_config_ui=not args.no_config_ui,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
