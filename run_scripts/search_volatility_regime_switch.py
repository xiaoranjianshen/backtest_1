# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import itertools
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from run_scripts.run_volatility_regime_switch import (
    DEFAULT_END_DATE,
    DEFAULT_START_DATE,
    all_main_symbols,
    build_strategy_kwargs,
    parse_symbols,
)
from strategy.custom.volatility_regime_switch import VolatilityRegimeSwitchStrategy


def _parse_csv_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def _parse_csv_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _parse_csv_strs(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _metric_float(metrics: dict, key: str) -> float:
    value = metrics.get(key)
    if value is None:
        return math.nan
    text = str(value).replace("%", "").replace(",", "").replace("￥", "").strip()
    if text in {"", "-", "inf"}:
        return math.inf if text == "inf" else math.nan
    try:
        return float(text)
    except ValueError:
        return math.nan


def _build_combo_args(base_args: argparse.Namespace, combo: dict) -> argparse.Namespace:
    data = vars(base_args).copy()
    data.update(combo)
    data["no_browser"] = True
    data["no_config_ui"] = True
    data["no_record_signals"] = True
    return argparse.Namespace(**data)


def _combo_grid(args: argparse.Namespace) -> list[dict]:
    if args.preset == "smoke":
        return [
            {
                "vol_window_days": 20,
                "regime_threshold": 0.70,
                "selection_count": 5,
                "reversion_model": "zscore",
                "trend_model": "donchian",
            },
            {
                "vol_window_days": 40,
                "regime_threshold": 0.80,
                "selection_count": 8,
                "reversion_model": "donchian_fade",
                "trend_model": "ma_cross",
            },
        ]

    vol_windows = _parse_csv_ints(args.vol_windows)
    thresholds = _parse_csv_floats(args.thresholds)
    counts = _parse_csv_ints(args.selection_counts)
    reversion_models = _parse_csv_strs(args.reversion_models)
    trend_models = _parse_csv_strs(args.trend_models)

    combos = [
        {
            "vol_window_days": vol_window,
            "regime_threshold": threshold,
            "selection_count": count,
            "reversion_model": reversion_model,
            "trend_model": trend_model,
        }
        for vol_window, threshold, count, reversion_model, trend_model
        in itertools.product(vol_windows, thresholds, counts, reversion_models, trend_models)
    ]
    if args.max_runs and args.max_runs > 0:
        combos = combos[: args.max_runs]
    return combos


def _run_one(args: argparse.Namespace, symbols: list[str], combo: dict) -> dict:
    combo_args = _build_combo_args(args, combo)
    strategy_kwargs = build_strategy_kwargs(combo_args, symbols)
    analyzer = run_backtest(
        strategy_class=VolatilityRegimeSwitchStrategy,
        symbols_input=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="1m",
        data_type="main",
        initial_capital=args.initial_capital,
        strategy_kwargs=strategy_kwargs,
        enable_main_rollover=True,
    )
    row = dict(combo)
    row["status"] = "no_trade" if analyzer is None else "ok"
    if analyzer is None:
        row.update({
            "sharpe": math.nan,
            "total_return_mtm_pct": math.nan,
            "max_drawdown_pct": math.nan,
            "trade_count": 0,
            "output_dir": "",
        })
        return row

    metrics = analyzer.metrics or {}
    row.update({
        "sharpe": _metric_float(metrics, "年化Sharpe"),
        "total_return_mtm_pct": _metric_float(metrics, "总收益(含持仓)"),
        "annual_return_mtm_pct": _metric_float(metrics, "年化收益(含持仓)"),
        "max_drawdown_pct": _metric_float(metrics, "最大回撤率"),
        "trade_count": int(metrics.get("交易次数", 0) or 0),
        "win_rate_trade_pct": _metric_float(metrics, "逐笔胜率"),
        "daily_win_rate_pct": _metric_float(metrics, "逐日胜率"),
        "output_dir": getattr(analyzer, "output_dir", ""),
    })
    return row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grid search volatility regime switch strategy.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--initial-capital", type=float, default=5_000_000.0)
    parser.add_argument("--preset", choices=["smoke", "coarse"], default="coarse")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--output-file", default=None)

    parser.add_argument("--vol-windows", default="5,10,20,60,120")
    parser.add_argument("--thresholds", default="0.60,0.70,0.80,0.90")
    parser.add_argument("--selection-counts", default="5,8,12")
    parser.add_argument("--reversion-models", default="zscore,donchian_fade,rsi")
    parser.add_argument("--trend-models", default="donchian,ma_cross,atr_breakout")

    parser.add_argument("--total-margin-target", type=float, default=0.30)
    parser.add_argument("--vol-percentile-lookback-days", type=int, default=252)
    parser.add_argument("--min-vol-percentile-samples", type=int, default=20)
    parser.add_argument("--rebalance-frequency", choices=["daily", "weekly", "monthly"], default="weekly")
    parser.add_argument("--confidence-weight", type=float, default=0.35)
    parser.add_argument("--min-selected-confidence", type=float, default=0.05)
    parser.add_argument("--min-avg-daily-notional", type=float, default=0.0)
    parser.add_argument("--min-daily-volatility", type=float, default=0.0)
    parser.add_argument("--max-symbol-notional-pct", type=float, default=2.0)
    parser.add_argument("--max-symbol-margin-pct", type=float, default=0.0)
    parser.add_argument("--excluded-symbols", default=None)
    parser.set_defaults(use_performance_selection=True)
    parser.add_argument("--use-performance-selection", dest="use_performance_selection", action="store_true")
    parser.add_argument("--no-performance-selection", dest="use_performance_selection", action="store_false")
    parser.add_argument("--performance-lookback-days", type=int, default=22)
    parser.add_argument("--performance-weight", type=float, default=0.50)
    parser.add_argument("--performance-min-trades", type=int, default=3)
    parser.add_argument("--performance-min-score", type=float, default=None)
    parser.add_argument("--exploration-count", type=int, default=2)
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
    parser.add_argument("--max-entries-per-symbol-per-day", type=int, default=3)
    parser.add_argument("--slippage-ticks", type=float, default=0.5)
    parser.add_argument("--build-best-report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = parse_symbols(args.symbols) if args.symbols else all_main_symbols()
    if args.max_symbols and args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]

    combos = _combo_grid(args)
    output_dir = PROJECT_ROOT / "exports" / "volatility_regime_search"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.output_file:
        output_path = Path(args.output_file)
        if not output_path.is_absolute():
            output_path = output_dir / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_path = output_dir / "volatility_regime_search_results.csv"
    rows = []

    print(f"[VolRegime Search] symbols={len(symbols)} | combos={len(combos)} | output={output_path}")
    for idx, combo in enumerate(combos, start=1):
        print(f"\n[VolRegime Search] combo {idx}/{len(combos)}: {combo}")
        row = _run_one(args, symbols, combo)
        rows.append(row)
        pd.DataFrame(rows).sort_values("sharpe", ascending=False).to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"[VolRegime Search] sharpe={row.get('sharpe')} trades={row.get('trade_count')}")

    result_df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    result_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print("\n[VolRegime Search] top results:")
    print(result_df.head(10).to_string(index=False))

    if args.build_best_report and not result_df.empty:
        best = result_df.iloc[0].to_dict()
        best_combo = {
            "vol_window_days": int(best["vol_window_days"]),
            "regime_threshold": float(best["regime_threshold"]),
            "selection_count": int(best["selection_count"]),
            "reversion_model": str(best["reversion_model"]),
            "trend_model": str(best["trend_model"]),
        }
        print(f"\n[VolRegime Search] rebuilding best report: {best_combo}")
        best_args = _build_combo_args(args, best_combo)
        best_args.no_record_signals = False
        analyzer = run_backtest(
            strategy_class=VolatilityRegimeSwitchStrategy,
            symbols_input=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            freq="1m",
            data_type="main",
            initial_capital=args.initial_capital,
            strategy_kwargs=build_strategy_kwargs(best_args, symbols),
            enable_main_rollover=True,
        )
        if analyzer is not None:
            build_html_dashboard(analyzer, open_browser=True, start_config_ui=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
