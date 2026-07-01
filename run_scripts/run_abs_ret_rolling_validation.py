# run_scripts/run_abs_ret_rolling_validation.py
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.abs_ret_rolling_validation import (
    AbsRetRollingValidationStrategy,
    DEFAULT_FEATURE_CACHE_PATH,
    DEFAULT_MONTHLY_VALIDATION_PATH,
    DEFAULT_MODEL_AVAILABLE_DATE,
    DEFAULT_ONLINE_FEATURES_PATH,
    DEFAULT_ONLINE_MODEL_PATH,
    DEFAULT_SIGNAL_PATH,
    DEFAULT_VALIDATION_PATH,
)
from ui.run_from_config import (
    _parse_symbols,
    _read_absret_allowed_products,
    _read_absret_feature_symbols,
    _read_absret_prediction_symbols,
)


DEFAULT_START_DATE = "2025-07-01"
DEFAULT_END_DATE = "2026-01-01"


def _optional_int(value: int) -> int | None:
    return None if int(value) <= 0 else int(value)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    signal_path = _blank_to_none(args.signal_path)
    feature_cache_path = _blank_to_none(args.feature_cache_path)
    validation_path = _blank_to_none(args.validation_path)
    allowed_products = _read_absret_allowed_products(
        validation_path=validation_path,
        model_name=args.model_name,
        min_validation_hit_rate=args.min_validation_hit_rate,
        validation_mode=args.validation_mode,
        monthly_validation_path=_blank_to_none(args.monthly_validation_path),
        validation_lookback_months=args.validation_lookback_months,
        min_validation_rows=args.min_validation_rows,
    )
    if args.prediction_mode in {"online", "online_model", "model"}:
        symbols = _read_absret_feature_symbols(
            feature_cache_path=feature_cache_path,
            selected_symbols=_parse_symbols(args.symbols) if args.symbols else [],
            allowed_products=allowed_products,
            max_symbols=args.max_symbols,
            universe_mode=args.universe_mode,
            start_date=args.start_date,
            end_date=args.end_date,
            model_available_date=args.model_available_date,
        )
    else:
        symbols = _read_absret_prediction_symbols(
            signal_path=signal_path,
            model_name=args.model_name,
            selected_symbols=_parse_symbols(args.symbols) if args.symbols else [],
            allowed_products=allowed_products,
            max_symbols=args.max_symbols,
            universe_mode=args.universe_mode,
        )
    if not symbols:
        raise SystemExit("No matched abs_ret feature/prediction symbols. Check --symbols/--model-name/validation threshold.")
    return symbols


def build_strategy_kwargs(args: argparse.Namespace, symbols: list[str]) -> dict:
    return {
        "target_symbols": symbols,
        "prediction_mode": args.prediction_mode,
        "signal_path": _blank_to_none(args.signal_path),
        "feature_cache_path": _blank_to_none(args.feature_cache_path),
        "model_path": _blank_to_none(args.model_path),
        "model_features_path": _blank_to_none(args.model_features_path),
        "model_available_date": _blank_to_none(args.model_available_date),
        "validation_path": _blank_to_none(args.validation_path),
        "monthly_validation_path": _blank_to_none(args.monthly_validation_path),
        "model_name": args.model_name,
        "min_validation_hit_rate": args.min_validation_hit_rate,
        "validation_mode": args.validation_mode,
        "validation_lookback_months": args.validation_lookback_months,
        "min_validation_rows": args.min_validation_rows,
        "signal_time_column": args.signal_time_column,
        "signal_frequency": args.signal_frequency,
        "daily_signal_policy": args.daily_signal_policy,
        "daily_signal_cutoff_hour": args.daily_signal_cutoff_hour,
        "min_signal_confidence": args.min_signal_confidence,
        "edge_quantile": args.edge_quantile,
        "edge_threshold_mode": args.edge_threshold_mode,
        "edge_threshold_lookback": args.edge_threshold_lookback,
        "min_threshold_history": args.min_threshold_history,
        "max_total_margin_pct": args.max_total_margin_pct,
        "max_positions": _optional_int(args.max_positions),
        "one_contract_per_product": not args.allow_multi_contract_per_product,
        "close_on_failed_signal": not args.hold_failed_signal,
        "sizing": {
            "mode": "fixed_volume",
            "value": 1,
            "min_volume": 0,
            "max_volume": None,
            "round_lot": 1,
        },
        "execution": {
            "order_type": args.order_type,
            "price_field": "close",
            "slippage_ticks": args.slippage_ticks,
        },
        "exit": {
            "close_pct": 1.0,
            "allow_reverse": True,
            "respect_pending_orders": True,
        },
        "record_signals": not args.no_record_signals,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily abs_ret model strategy with rolling validation filter.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--initial-capital", type=float, default=10_000_000.0)
    parser.add_argument("--symbols", default="", help="Products or concrete contracts. Blank uses validated products.")
    parser.add_argument("--max-symbols", type=int, default=0, help="0 means all matched prediction contracts.")
    parser.add_argument("--prediction-mode", choices=["online_model", "replay_csv"], default="online_model")
    parser.add_argument("--model-name", default="hybrid_product")
    parser.add_argument("--signal-path", default=str(DEFAULT_SIGNAL_PATH))
    parser.add_argument("--feature-cache-path", default=str(DEFAULT_FEATURE_CACHE_PATH))
    parser.add_argument("--model-path", default=str(DEFAULT_ONLINE_MODEL_PATH))
    parser.add_argument("--model-features-path", default=str(DEFAULT_ONLINE_FEATURES_PATH))
    parser.add_argument("--model-available-date", default=DEFAULT_MODEL_AVAILABLE_DATE)
    parser.add_argument("--validation-path", default=str(DEFAULT_VALIDATION_PATH))
    parser.add_argument("--monthly-validation-path", default=str(DEFAULT_MONTHLY_VALIDATION_PATH))
    parser.add_argument("--min-validation-hit-rate", type=float, default=0.60)
    parser.add_argument("--validation-mode", choices=["monthly_prior", "aggregate"], default="monthly_prior")
    parser.add_argument("--validation-lookback-months", type=int, default=3)
    parser.add_argument("--min-validation-rows", type=int, default=30)
    parser.add_argument("--signal-time-column", choices=["end_datetime", "start_datetime"], default="end_datetime")
    parser.add_argument("--signal-frequency", choices=["daily", "intraday"], default="daily")
    parser.add_argument("--daily-signal-policy", choices=["strongest", "last", "first"], default="strongest")
    parser.add_argument("--daily-signal-cutoff-hour", type=int, default=21)
    parser.add_argument("--min-signal-confidence", type=float, default=0.60)
    parser.add_argument("--universe-mode", choices=["all_predictions", "validated_products"], default="all_predictions")
    parser.add_argument("--edge-quantile", type=float, default=0.90)
    parser.add_argument("--edge-threshold-mode", choices=["rolling", "static", "none"], default="rolling")
    parser.add_argument("--edge-threshold-lookback", type=int, default=5000)
    parser.add_argument("--min-threshold-history", type=int, default=200)
    parser.add_argument("--max-total-margin-pct", type=float, default=0.30)
    parser.add_argument("--max-positions", type=int, default=0)
    parser.add_argument("--allow-multi-contract-per-product", action="store_true")
    parser.add_argument("--hold-failed-signal", action="store_true")
    parser.add_argument("--order-type", choices=["market", "limit"], default="market")
    parser.add_argument("--slippage-ticks", type=float, default=0.5)
    parser.add_argument("--no-record-signals", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Resolve symbols and print config without running backtest.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    target_symbols = resolve_symbols(args)
    strategy_kwargs = build_strategy_kwargs(args, target_symbols)

    print("[AbsRet Run] symbols=", len(target_symbols), target_symbols[:20])
    print("[AbsRet Run] model=", args.model_name)
    print("[AbsRet Run] prediction_mode=", args.prediction_mode)
    print("[AbsRet Run] validation_mode=", args.validation_mode)
    print("[AbsRet Run] execution= daily bars on all contracts")
    print("[AbsRet Run] signal_frequency=", args.signal_frequency, "| daily_policy=", args.daily_signal_policy)
    print("[AbsRet Run] universe_mode=", args.universe_mode, "| min_signal_confidence=", args.min_signal_confidence)
    print("[AbsRet Run] window=", args.start_date, "~", args.end_date)
    print("[AbsRet Run] max_total_margin_pct=", args.max_total_margin_pct)
    if args.dry_run:
        print("[AbsRet Run] dry-run only, no backtest executed.")
        raise SystemExit(0)

    analyzer = run_backtest(
        strategy_class=AbsRetRollingValidationStrategy,
        symbols_input=target_symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        freq="1d",
        data_type="all",
        initial_capital=args.initial_capital,
        strategy_kwargs=strategy_kwargs,
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
