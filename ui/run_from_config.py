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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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
