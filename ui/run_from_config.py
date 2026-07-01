# -*- coding: utf-8 -*-
"""
Build and run backtests from a JSON configuration file.

The Streamlit UI writes plain JSON. This module is the only place that maps
UI fields to strategy constructor kwargs, so future strategies can be added
without creating another demo script for every parameter combination.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from config import pure_product_code


UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
project_path = str(PROJECT_ROOT)
if project_path in sys.path:
    sys.path.remove(project_path)
sys.path.insert(0, project_path)

loaded_config = sys.modules.get("config")
if loaded_config is not None:
    loaded_path = Path(getattr(loaded_config, "__file__", "") or "")
    if loaded_path.name == "config.py" and loaded_path.parent == UI_DIR:
        del sys.modules["config"]


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    module: str
    class_name: str
    kind: str
    builder: Callable[[dict[str, Any], "StrategySpec"], dict[str, Any]]


def _import_class(module_name: str, class_name: str):
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _class_available(module_name: str, class_name: str) -> bool:
    try:
        _import_class(module_name, class_name)
        return True
    except Exception:
        return False


def _parse_symbols(value) -> list[str]:
    if value is None:
        return ["rb"]
    if isinstance(value, str):
        text = (
            value.replace("，", ",")
            .replace("、", ",")
            .replace("；", ",")
            .replace(";", ",")
            .replace("\n", ",")
            .replace(" ", ",")
        )
        symbols = [item.strip().lower() for item in text.split(",") if item.strip()]
    else:
        symbols = [str(item).strip().lower() for item in value if str(item).strip()]
    return symbols or ["rb"]


def _date_text(value, default: str) -> str:
    text = str(value or default)
    return text if " " in text else f"{text} 00:00:00"


def _end_date_text(value, default: str) -> str:
    text = str(value or default)
    return text if " " in text else f"{text} 23:59:59"


def _base_args(config: dict[str, Any]) -> dict[str, Any]:
    freq = config.get("freq", "1d")
    data_type = config.get("data_type", "main")
    if freq == "tick" and data_type != "main":
        raise ValueError("Tick backtest currently supports data_type='main' only.")

    return {
        "start_date": _date_text(config.get("start_date"), "2021-01-01 00:00:00"),
        "end_date": _end_date_text(config.get("end_date"), "2022-01-01 23:59:59"),
        "freq": freq,
        "data_type": data_type,
        "initial_capital": float(config.get("initial_capital", 5_000_000.0)),
        "enable_main_rollover": bool(config.get("enable_main_rollover", True)),
    }


def _optional_int(value):
    if value in (None, "", "None"):
        return None
    return int(value)


def _optional_path(value):
    if value in (None, "", "None"):
        return None
    return str(value)


def _market_execution(config: dict[str, Any]) -> dict[str, Any]:
    order_type = str(config.get("order_type", "market")).lower()
    if order_type not in {"market", "opponent", "limit"}:
        raise ValueError(f"Unsupported order_type: {order_type}")
    if order_type == "opponent" and config.get("freq", "1d") != "tick":
        raise ValueError("order_type='opponent' is only supported for tick backtests.")

    execution = {
        "order_type": order_type,
        "price_field": config.get("price_field", "close"),
        "slippage_ticks": float(config.get("slippage_ticks", 1.0)),
    }
    if order_type == "limit":
        execution.update({
            "limit_mode": config.get("limit_mode", "at_close"),
            "ticks": float(config.get("limit_ticks", 0.0)),
        })
    return execution


def _sizing(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": config.get("sizing_mode", "equity_pct"),
        "value": float(config.get("sizing_value", 0.03)),
        "min_volume": int(config.get("min_volume", 1)),
        "max_volume": _optional_int(config.get("max_volume")),
        "round_lot": int(config.get("round_lot", 1)),
    }


def _exit(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "close_pct": float(config.get("close_pct", 1.0)),
        "allow_reverse": bool(config.get("allow_reverse", True)),
        "respect_pending_orders": bool(config.get("respect_pending_orders", True)),
    }


def _general_signal_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "sizing": _sizing(config),
        "execution": _market_execution(config),
        "exit": _exit(config),
        "record_signals": bool(config.get("record_signals", True)),
    }


def _build_general_multi_ma(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    symbols = _parse_symbols(config.get("symbols", config.get("factor_symbols", config.get("symbol"))))
    args = _base_args(config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "fast_window": int(config.get("fast_window", 10)),
            "slow_window": int(config.get("slow_window", 30)),
            **_general_signal_kwargs(config),
        },
    })
    return args


def _build_breakout_pyramid(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    symbol = _parse_symbols(config.get("symbols", config.get("symbol")))[0]
    args = _base_args(config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbol,
        "strategy_kwargs": {
            "lookback": int(config.get("lookback", 20)),
            "add_scale": float(config.get("add_scale", 1.0)),
            "max_position_scale": float(config.get("max_position_scale", 4.0)),
            "allow_short": bool(config.get("allow_short", True)),
            **_general_signal_kwargs(config),
        },
    })
    return args


def _build_dual_ma(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    symbol = _parse_symbols(config.get("symbols", config.get("symbol")))[0]
    args = _base_args(config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbol,
        "strategy_kwargs": {
            "fast_window": int(config.get("fast_window", 10)),
            "slow_window": int(config.get("slow_window", 30)),
            **_general_signal_kwargs(config),
        },
    })
    return args


def _build_zscore_reversal(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    symbols = _parse_symbols(config.get("symbols", config.get("symbol")))
    args = _base_args(config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "lookback": int(config.get("lookback", 10)),
            "entry_z": float(config.get("entry_z", 2.1)),
            "first_exit_z": float(config.get("first_exit_z", 0.0)),
            "final_exit_z": float(config.get("final_exit_z", 1.0)),
            **_general_signal_kwargs(config),
        },
    })
    return args


def _build_tick_anomaly_scalping(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config["freq"] = "tick"
    local_config["data_type"] = "main"
    local_config.setdefault("price_field", "mid_price")
    local_config.setdefault("order_type", "opponent")
    local_config.setdefault("slippage_ticks", 0.0)

    raw_symbols = local_config.get("symbols") or local_config.get("symbol") or ["au"]
    symbols = _parse_symbols(raw_symbols)
    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "scalp_mode": local_config.get("scalp_mode", "reversal"),
            "shock_window_seconds": float(local_config.get("shock_window_seconds", 3.0)),
            "lookback_days": float(local_config.get("lookback_days", 10.0)),
            "tail_prob": float(local_config.get("tail_prob", 0.001)),
            "min_move_bps": float(local_config.get("min_move_bps", 4.0)),
            "min_history_samples": int(local_config.get("min_history_samples", 5000)),
            "directional_ratio": float(local_config.get("directional_ratio", 0.75)),
            "max_spread_ticks": float(local_config.get("max_spread_ticks", 3.0)),
            "hold_seconds": float(local_config.get("hold_seconds", 20.0)),
            "take_profit_ticks": float(local_config.get("take_profit_ticks", 8.0)),
            "stop_loss_ticks": float(local_config.get("stop_loss_ticks", 5.0)),
            "cooldown_seconds": float(local_config.get("cooldown_seconds", 20.0)),
            "threshold_refresh_ticks": int(local_config.get("threshold_refresh_ticks", 100)),
            "pause_seconds": float(local_config.get("pause_seconds", 1.0)),
            "reversal_confirm_seconds": float(local_config.get("reversal_confirm_seconds", 1.5)),
            "reversal_retrace_ratio": float(local_config.get("reversal_retrace_ratio", 0.4)),
            "reversal_min_retrace_ticks": float(local_config.get("reversal_min_retrace_ticks", 2.0)),
            "require_history_ready": bool(local_config.get("require_history_ready", True)),
            "warmup_days": float(local_config.get("warmup_days", 10.0)),
            **_general_signal_kwargs(local_config),
        },
    })
    return args


def _build_utbot_stc_hull(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config.setdefault("freq", "5m")
    local_config.setdefault("data_type", "main")
    local_config.setdefault("order_type", "market")
    local_config.setdefault("price_field", "close")
    local_config.setdefault("slippage_ticks", 0.5)

    symbols = _parse_symbols(local_config.get("symbols") or local_config.get("symbol") or ["au", "ag"])
    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "hma_length": int(local_config.get("hma_length", 55)),
            "atr_period": int(local_config.get("atr_period", 10)),
            "ut_key_value": float(local_config.get("ut_key_value", 1.0)),
            "stc_length": int(local_config.get("stc_length", 12)),
            "stc_fast": int(local_config.get("stc_fast", 26)),
            "stc_slow": int(local_config.get("stc_slow", 50)),
            "stc_factor": float(local_config.get("stc_factor", 0.5)),
            "stc_long_max": float(local_config.get("stc_long_max", 35.0)),
            "stc_short_min": float(local_config.get("stc_short_min", 65.0)),
            "require_price_above_hull": bool(local_config.get("require_price_above_hull", True)),
            "exit_on_opposite_signal": bool(local_config.get("exit_on_opposite_signal", True)),
            "take_profit_ticks": float(local_config.get("take_profit_ticks", 24.0)),
            "stop_loss_ticks": float(local_config.get("stop_loss_ticks", 16.0)),
            "max_hold_bars": int(local_config.get("max_hold_bars", 36)),
            "cooldown_bars": int(local_config.get("cooldown_bars", 3)),
            "max_entries_per_symbol_per_day": _optional_int(local_config.get("max_entries_per_symbol_per_day", 20)),
            **_general_signal_kwargs(local_config),
        },
    })
    return args


def _build_vwap_band_reversion(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config.setdefault("freq", "5m")
    local_config.setdefault("data_type", "main")
    local_config.setdefault("order_type", "market")
    local_config.setdefault("price_field", "close")
    local_config.setdefault("slippage_ticks", 0.5)

    symbols = _parse_symbols(local_config.get("symbols") or local_config.get("symbol") or ["au", "ag"])
    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "std_window": int(local_config.get("std_window", 48)),
            "entry_z": float(local_config.get("entry_z", 2.0)),
            "exit_z": float(local_config.get("exit_z", 0.25)),
            "min_bars_in_session": int(local_config.get("min_bars_in_session", 12)),
            "min_std_ticks": float(local_config.get("min_std_ticks", 4.0)),
            "max_vwap_slope_ticks": float(local_config.get("max_vwap_slope_ticks", 6.0)),
            "slope_window": int(local_config.get("slope_window", 6)),
            "take_profit_ticks": float(local_config.get("take_profit_ticks", 28.0)),
            "stop_loss_ticks": float(local_config.get("stop_loss_ticks", 18.0)),
            "max_hold_bars": int(local_config.get("max_hold_bars", 24)),
            "cooldown_bars": int(local_config.get("cooldown_bars", 3)),
            "max_entries_per_symbol_per_day": _optional_int(local_config.get("max_entries_per_symbol_per_day", 20)),
            "session_start_hour": int(local_config.get("session_start_hour", 21)),
            **_general_signal_kwargs(local_config),
        },
    })
    return args


def _build_donchian_atr_breakout(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config.setdefault("freq", "5m")
    local_config.setdefault("data_type", "main")
    local_config.setdefault("order_type", "market")
    local_config.setdefault("price_field", "close")
    local_config.setdefault("slippage_ticks", 0.5)

    symbols = _parse_symbols(local_config.get("symbols") or local_config.get("symbol") or ["au", "ag"])
    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "donchian_window": int(local_config.get("donchian_window", 144)),
            "atr_period": int(local_config.get("atr_period", 72)),
            "trend_window": int(local_config.get("trend_window", 240)),
            "breakout_buffer_ticks": float(local_config.get("breakout_buffer_ticks", 2.0)),
            "min_channel_atr": float(local_config.get("min_channel_atr", 1.8)),
            "max_extension_atr": float(local_config.get("max_extension_atr", 1.5)),
            "atr_stop_mult": float(local_config.get("atr_stop_mult", 3.2)),
            "exit_on_midline": bool(local_config.get("exit_on_midline", True)),
            "max_hold_bars": int(local_config.get("max_hold_bars", 384)),
            "cooldown_bars": int(local_config.get("cooldown_bars", 18)),
            "max_entries_per_symbol_per_day": _optional_int(local_config.get("max_entries_per_symbol_per_day", 3)),
            "allowed_entry_hours": local_config.get("allowed_entry_hours", "9,10,13,21,22,23,0,1,2"),
            **_general_signal_kwargs(local_config),
        },
    })
    return args


def _build_opening_range_acd(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config.setdefault("freq", "5m")
    local_config.setdefault("data_type", "main")
    local_config.setdefault("order_type", "market")
    local_config.setdefault("price_field", "close")
    local_config.setdefault("slippage_ticks", 0.5)

    symbols = _parse_symbols(local_config.get("symbols") or local_config.get("symbol") or ["au", "ag"])
    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "opening_range_bars": int(local_config.get("opening_range_bars", 3)),
            "atr_period": int(local_config.get("atr_period", 48)),
            "trend_window": int(local_config.get("trend_window", 144)),
            "breakout_buffer_ticks": float(local_config.get("breakout_buffer_ticks", 1.0)),
            "min_range_atr": float(local_config.get("min_range_atr", 0.5)),
            "max_extension_atr": float(local_config.get("max_extension_atr", 1.2)),
            "atr_stop_mult": float(local_config.get("atr_stop_mult", 3.0)),
            "trail_atr_mult": float(local_config.get("trail_atr_mult", 4.0)),
            "take_profit_atr": float(local_config.get("take_profit_atr", 0.0)),
            "exit_on_range_reentry": bool(local_config.get("exit_on_range_reentry", False)),
            "max_hold_bars": int(local_config.get("max_hold_bars", 384)),
            "cooldown_bars": int(local_config.get("cooldown_bars", 18)),
            "max_entries_per_symbol_per_day": _optional_int(local_config.get("max_entries_per_symbol_per_day", 2)),
            "session_start_hours": local_config.get("session_start_hours", "9,21"),
            "allowed_entry_hours": local_config.get("allowed_entry_hours", "9,10,21,22,23,0,1"),
            **_general_signal_kwargs(local_config),
        },
    })
    return args


def _normalize_absret_symbol(symbol: object) -> str:
    text = str(symbol).strip()
    match = re.search(r"\((.*?)\)", text)
    if match:
        text = match.group(1)
    text = text.split(".")[-1]
    return text.lower()


def _is_absret_contract(symbol: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]+\d+$", str(symbol).strip()))


def _read_absret_allowed_products(
    validation_path: str | None,
    model_name: str,
    min_validation_hit_rate: float,
    validation_mode: str = "aggregate",
    monthly_validation_path: str | None = None,
    validation_lookback_months: int = 3,
    min_validation_rows: int = 30,
) -> set[str]:
    if str(validation_mode).strip().lower() == "monthly_prior":
        if not monthly_validation_path:
            from strategy.custom.abs_ret_rolling_validation import DEFAULT_MONTHLY_VALIDATION_PATH

            monthly_validation_path = str(DEFAULT_MONTHLY_VALIDATION_PATH)

        monthly_path = Path(monthly_validation_path)
        if monthly_path.exists():
            df = pd.read_csv(monthly_path, encoding="utf-8-sig")
            model_col = "experiment" if "experiment" in df.columns else "model" if "model" in df.columns else None
            if model_col is not None:
                df = df[df[model_col].astype(str) == str(model_name)].copy()
            if "slice" in df.columns:
                df = df[df["slice"].astype(str) == "product_top10"].copy()
            required = {"product", "month", "hit_rate", "rows"}
            if required.issubset(df.columns) and not df.empty:
                df["product_key"] = df["product"].map(pure_product_code)
                df["period"] = pd.to_datetime(df["month"], errors="coerce").dt.to_period("M")
                df["hit_rate"] = pd.to_numeric(df["hit_rate"], errors="coerce")
                df["rows"] = pd.to_numeric(df["rows"], errors="coerce").fillna(0.0)
                df = df.dropna(subset=["period", "hit_rate"]).sort_values(["product_key", "period"])

                lookback = max(1, int(validation_lookback_months))
                min_rows = max(0, int(min_validation_rows))
                allowed: set[str] = set()
                for period in sorted(df["period"].dropna().unique()):
                    for product, product_df in df.groupby("product_key", sort=False):
                        past = product_df[product_df["period"] < period].tail(lookback)
                        total_rows = float(past["rows"].sum())
                        if past.empty or total_rows < min_rows:
                            continue
                        weighted_hit = float((past["hit_rate"] * past["rows"]).sum() / total_rows)
                        if weighted_hit > float(min_validation_hit_rate):
                            allowed.add(product)
                return allowed

    if not validation_path:
        from strategy.custom.abs_ret_rolling_validation import DEFAULT_VALIDATION_PATH

        validation_path = str(DEFAULT_VALIDATION_PATH)

    df = pd.read_csv(validation_path, encoding="utf-8-sig")
    model_col = "experiment" if "experiment" in df.columns else "model" if "model" in df.columns else None
    if model_col is not None:
        df = df[df[model_col].astype(str) == str(model_name)].copy()

    rate_col = "weighted_hit_rate" if "weighted_hit_rate" in df.columns else "hit_rate" if "hit_rate" in df.columns else None
    if rate_col is None:
        raise ValueError(f"Validation file has no hit-rate column: {validation_path}")

    return {
        pure_product_code(product)
        for product, hit_rate in zip(df["product"], pd.to_numeric(df[rate_col], errors="coerce"))
        if pd.notna(hit_rate) and float(hit_rate) > float(min_validation_hit_rate)
    }


def _read_absret_prediction_symbols(
    signal_path: str | None,
    model_name: str,
    selected_symbols: list[str],
    allowed_products: set[str],
    max_symbols: int = 0,
    universe_mode: str = "all_predictions",
) -> list[str]:
    if not signal_path:
        from strategy.custom.abs_ret_rolling_validation import DEFAULT_SIGNAL_PATH

        signal_path = str(DEFAULT_SIGNAL_PATH)

    selected_symbols = [_normalize_absret_symbol(item) for item in selected_symbols if str(item).strip()]
    exact_symbols = {item for item in selected_symbols if _is_absret_contract(item)}
    selected_products = {pure_product_code(item) for item in selected_symbols if not _is_absret_contract(item)}
    use_all_predictions = str(universe_mode).strip().lower() == "all_predictions" and not selected_symbols
    product_filter = selected_products or (None if use_all_predictions else set(allowed_products))
    if not exact_symbols and product_filter is not None and not product_filter:
        return []

    usecols = ["symbol", "product"]
    header = pd.read_csv(signal_path, nrows=0, encoding="utf-8-sig").columns.tolist()
    model_col = "experiment" if "experiment" in header else "model" if "model" in header else None
    if model_col is not None:
        usecols.append(model_col)

    symbols: set[str] = set()
    for chunk in pd.read_csv(signal_path, usecols=usecols, encoding="utf-8-sig", chunksize=500_000):
        if model_col is not None:
            chunk = chunk[chunk[model_col].astype(str) == str(model_name)]
        if chunk.empty:
            continue

        chunk["symbol_key"] = chunk["symbol"].map(_normalize_absret_symbol)
        chunk["product_key"] = chunk["product"].map(pure_product_code)
        if exact_symbols:
            chunk = chunk[chunk["symbol_key"].isin(exact_symbols)]
        elif product_filter is not None:
            chunk = chunk[chunk["product_key"].isin(product_filter)]
        if chunk.empty:
            continue

        symbols.update(chunk["symbol_key"].dropna().astype(str).tolist())
        if max_symbols > 0 and len(symbols) >= max_symbols:
            break

    ordered = sorted(symbols)
    return ordered[:max_symbols] if max_symbols > 0 else ordered


def _read_absret_feature_symbols(
    feature_cache_path: str | None,
    selected_symbols: list[str],
    allowed_products: set[str],
    max_symbols: int = 0,
    universe_mode: str = "all_predictions",
    start_date: str | None = None,
    end_date: str | None = None,
    model_available_date: str | None = None,
) -> list[str]:
    if not feature_cache_path:
        from strategy.custom.abs_ret_rolling_validation import DEFAULT_FEATURE_CACHE_PATH

        feature_cache_path = str(DEFAULT_FEATURE_CACHE_PATH)

    selected_symbols = [_normalize_absret_symbol(item) for item in selected_symbols if str(item).strip()]
    exact_symbols = {item for item in selected_symbols if _is_absret_contract(item)}
    selected_products = {pure_product_code(item) for item in selected_symbols if not _is_absret_contract(item)}
    use_all_features = str(universe_mode).strip().lower() == "all_predictions" and not selected_symbols
    product_filter = selected_products or (None if use_all_features else set(allowed_products))
    if not exact_symbols and product_filter is not None and not product_filter:
        return []

    df = pd.read_parquet(feature_cache_path, columns=["symbol", "product", "end_datetime"])
    if df.empty:
        return []

    df["end_datetime"] = pd.to_datetime(df["end_datetime"], errors="coerce")
    lower_bound = pd.to_datetime(model_available_date or start_date, errors="coerce")
    start_bound = pd.to_datetime(start_date, errors="coerce")
    if pd.notna(lower_bound) and pd.notna(start_bound):
        lower_bound = max(lower_bound, start_bound)
    if pd.notna(lower_bound):
        df = df[df["end_datetime"] >= lower_bound]
    end_bound = pd.to_datetime(end_date, errors="coerce")
    if pd.notna(end_bound):
        df = df[df["end_datetime"] <= end_bound]
    if df.empty:
        return []

    df = df[["symbol", "product"]].drop_duplicates().copy()
    df["symbol_key"] = df["symbol"].map(_normalize_absret_symbol)
    df["product_key"] = df["product"].map(pure_product_code)
    if exact_symbols:
        df = df[df["symbol_key"].isin(exact_symbols)]
    elif product_filter is not None:
        df = df[df["product_key"].isin(product_filter)]

    ordered = sorted(set(df["symbol_key"].dropna().astype(str).tolist()))
    return ordered[:max_symbols] if max_symbols > 0 else ordered


def _build_abs_ret_rolling_validation(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    local_config = dict(config)
    local_config["freq"] = "1d"
    local_config["data_type"] = "all"
    local_config.setdefault("order_type", "market")
    local_config.setdefault("price_field", "close")
    local_config.setdefault("slippage_ticks", 0.5)

    model_name = str(local_config.get("model_name", "hybrid_product"))
    prediction_mode = str(local_config.get("prediction_mode", "online_model"))
    min_hit_rate = float(local_config.get("min_validation_hit_rate", 0.60))
    universe_mode = str(local_config.get("absret_universe_mode", "all_predictions"))
    signal_path = _optional_path(local_config.get("signal_path"))
    feature_cache_path = _optional_path(local_config.get("feature_cache_path"))
    model_path = _optional_path(local_config.get("model_path"))
    model_features_path = _optional_path(local_config.get("model_features_path"))
    validation_path = _optional_path(local_config.get("validation_path"))
    monthly_validation_path = _optional_path(local_config.get("monthly_validation_path"))

    validation_mode = str(local_config.get("validation_mode", "monthly_prior"))
    validation_lookback_months = int(local_config.get("validation_lookback_months", 3))
    min_validation_rows = int(local_config.get("min_validation_rows", 30))
    allowed_products = _read_absret_allowed_products(
        validation_path=validation_path,
        model_name=model_name,
        min_validation_hit_rate=min_hit_rate,
        validation_mode=validation_mode,
        monthly_validation_path=monthly_validation_path,
        validation_lookback_months=validation_lookback_months,
        min_validation_rows=min_validation_rows,
    )
    selected_raw = local_config.get("symbols") or local_config.get("symbol") or []
    selected_symbols = [] if selected_raw in (None, "", []) else _parse_symbols(selected_raw)
    if prediction_mode in {"online", "online_model", "model"}:
        symbols = _read_absret_feature_symbols(
            feature_cache_path=feature_cache_path,
            selected_symbols=selected_symbols,
            allowed_products=allowed_products,
            max_symbols=int(local_config.get("absret_max_symbols", 0) or 0),
            universe_mode=universe_mode,
            start_date=local_config.get("start_date"),
            end_date=local_config.get("end_date"),
            model_available_date=local_config.get("model_available_date", "2025-07-01"),
        )
    else:
        symbols = _read_absret_prediction_symbols(
            signal_path=signal_path,
            model_name=model_name,
            selected_symbols=selected_symbols,
            allowed_products=allowed_products,
            max_symbols=int(local_config.get("absret_max_symbols", 0) or 0),
            universe_mode=universe_mode,
        )
    if not symbols:
        raise ValueError("No abs_ret feature/prediction symbols matched the selected products/contracts.")

    args = _base_args(local_config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "prediction_mode": prediction_mode,
            "signal_path": signal_path,
            "feature_cache_path": feature_cache_path,
            "model_path": model_path,
            "model_features_path": model_features_path,
            "model_available_date": local_config.get("model_available_date", "2025-07-01"),
            "validation_path": validation_path,
            "monthly_validation_path": monthly_validation_path,
            "model_name": model_name,
            "min_validation_hit_rate": min_hit_rate,
            "validation_mode": validation_mode,
            "validation_lookback_months": validation_lookback_months,
            "min_validation_rows": min_validation_rows,
            "signal_time_column": local_config.get("signal_time_column", "end_datetime"),
            "signal_frequency": local_config.get("signal_frequency", "daily"),
            "daily_signal_policy": local_config.get("daily_signal_policy", "strongest"),
            "daily_signal_cutoff_hour": int(local_config.get("daily_signal_cutoff_hour", 21)),
            "min_signal_confidence": float(local_config.get("min_signal_confidence", 0.60)),
            "edge_quantile": float(local_config.get("edge_quantile", 0.90)),
            "edge_threshold_mode": local_config.get("edge_threshold_mode", "rolling"),
            "edge_threshold_lookback": int(local_config.get("edge_threshold_lookback", 5000)),
            "min_threshold_history": int(local_config.get("min_threshold_history", 200)),
            "max_total_margin_pct": float(local_config.get("max_total_margin_pct", 0.30)),
            "max_positions": _optional_int(local_config.get("max_positions")),
            "one_contract_per_product": bool(local_config.get("one_contract_per_product", True)),
            "close_on_failed_signal": bool(local_config.get("close_on_failed_signal", True)),
            "sizing": {
                "mode": "fixed_volume",
                "value": 1,
                "min_volume": 0,
                "max_volume": _optional_int(local_config.get("max_volume")),
                "round_lot": 1,
            },
            "execution": _market_execution(local_config),
            "exit": _exit(local_config),
            "record_signals": bool(local_config.get("record_signals", True)),
        },
    })
    return args


def _build_factor(config: dict[str, Any], spec: StrategySpec) -> dict[str, Any]:
    symbols = _parse_symbols(config.get("symbols", config.get("factor_symbols", config.get("symbol"))))
    args = _base_args(config)
    args.update({
        "strategy_class": _import_class(spec.module, spec.class_name),
        "symbols_input": symbols,
        "strategy_kwargs": {
            "target_symbols": symbols,
            "rebalance_period": int(config.get("rebalance_period", 5)),
            "top_k": int(config.get("top_k", 2)),
            "signal_scale": float(config.get("signal_scale", 1.0)),
            **_general_signal_kwargs(config),
        },
    })
    return args


STRATEGY_SPECS = {
    "general_multi_ma": StrategySpec(
        key="general_multi_ma",
        label="通用多品种均线 (General Multi MA)",
        module="strategy.custom.general_multi_ma",
        class_name="GeneralMultiMAStrategy",
        kind="general_signal",
        builder=_build_general_multi_ma,
    ),
    "breakout_pyramid": StrategySpec(
        key="breakout_pyramid",
        label="增仓突破 (Breakout Pyramid)",
        module="strategy.custom.breakout_pyramid",
        class_name="BreakoutPyramidStrategy",
        kind="general_signal",
        builder=_build_breakout_pyramid,
    ),
    "dual_ma": StrategySpec(
        key="dual_ma",
        label="双均线策略 (Dual MA)",
        module="strategy.custom.dual_ma",
        class_name="DualMAStrategy",
        kind="general_signal",
        builder=_build_dual_ma,
    ),
    "zscore_reversal": StrategySpec(
        key="zscore_reversal",
        label="ZScore 反转 (Z-Score Reversal)",
        module="strategy.custom.zscore_reversal",
        class_name="ZScoreReversalStrategy",
        kind="general_signal",
        builder=_build_zscore_reversal,
    ),
    "tick_anomaly_scalping": StrategySpec(
        key="tick_anomaly_scalping",
        label="Tick Anomaly Scalping",
        module="strategy.custom.tick_anomaly_scalping",
        class_name="TickAnomalyScalpingStrategy",
        kind="general_signal",
        builder=_build_tick_anomaly_scalping,
    ),
    "utbot_stc_hull": StrategySpec(
        key="utbot_stc_hull",
        label="UT Bot + STC + Hull (5m)",
        module="strategy.custom.utbot_stc_hull",
        class_name="UTBotSTCHullStrategy",
        kind="general_signal",
        builder=_build_utbot_stc_hull,
    ),
    "vwap_band_reversion": StrategySpec(
        key="vwap_band_reversion",
        label="VWAP Band Reversion (5m)",
        module="strategy.custom.vwap_band_reversion",
        class_name="VWAPBandReversionStrategy",
        kind="general_signal",
        builder=_build_vwap_band_reversion,
    ),
    "donchian_atr_breakout": StrategySpec(
        key="donchian_atr_breakout",
        label="Donchian ATR Breakout (5m)",
        module="strategy.custom.donchian_atr_breakout",
        class_name="DonchianATRBreakoutStrategy",
        kind="general_signal",
        builder=_build_donchian_atr_breakout,
    ),
    "opening_range_acd": StrategySpec(
        key="opening_range_acd",
        label="Opening Range ACD (5m)",
        module="strategy.custom.opening_range_acd",
        class_name="OpeningRangeACDStrategy",
        kind="general_signal",
        builder=_build_opening_range_acd,
    ),
    "abs_ret_rolling_validation": StrategySpec(
        key="abs_ret_rolling_validation",
        label="AbsRet Rolling Validation (1d all contracts)",
        module="strategy.custom.abs_ret_rolling_validation",
        class_name="AbsRetRollingValidationStrategy",
        kind="general_signal",
        builder=_build_abs_ret_rolling_validation,
    ),
    "composite_factor": StrategySpec(
        key="composite_factor",
        label="复合因子 (Composite Factor)",
        module="strategy.factor_template.composite_factor",
        class_name="CompositeFactorStrategy",
        kind="general_signal",
        builder=_build_factor,
    ),
    "cross_momentum": StrategySpec(
        key="cross_momentum",
        label="截面动量反转 (Cross Momentum)",
        module="strategy.factor_template.cross_momentum",
        class_name="CrossMomentumFactor",
        kind="general_signal",
        builder=_build_factor,
    ),
}


def available_strategy_specs() -> list[dict[str, str]]:
    available = []
    for spec in STRATEGY_SPECS.values():
        if _class_available(spec.module, spec.class_name):
            available.append({
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "class_name": spec.class_name,
            })
    return available


def available_strategy_keys() -> set[str]:
    return {item["key"] for item in available_strategy_specs()}


def build_run_arguments(config: dict[str, Any]) -> dict[str, Any]:
    strategy_key = str(config.get("strategy", "general_multi_ma")).strip().lower()
    spec = STRATEGY_SPECS.get(strategy_key)
    if spec is None:
        raise ValueError(f"Unsupported strategy: {strategy_key}")
    if not _class_available(spec.module, spec.class_name):
        raise ValueError(f"Strategy is unavailable: {strategy_key} ({spec.module}.{spec.class_name})")
    return spec.builder(config, spec)


def run_from_config(config_path: str):
    from backtest_engine import run_backtest
    from frontend_index import build_html_dashboard

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    args = build_run_arguments(config)
    analyzer = run_backtest(**args)
    dashboard_path = build_html_dashboard(analyzer, open_browser=False)
    if dashboard_path:
        print(f"DASHBOARD_URL: {Path(dashboard_path).resolve().as_uri()}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python ui/run_from_config.py <config.json>")
    run_from_config(sys.argv[1])
