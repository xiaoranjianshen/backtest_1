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
    return {
        "start_date": _date_text(config.get("start_date"), "2021-01-01 00:00:00"),
        "end_date": _end_date_text(config.get("end_date"), "2022-01-01 23:59:59"),
        "freq": config.get("freq", "1d"),
        "data_type": config.get("data_type", "main"),
        "initial_capital": float(config.get("initial_capital", 5_000_000.0)),
        "enable_main_rollover": bool(config.get("enable_main_rollover", True)),
    }


def _optional_int(value):
    if value in (None, "", "None"):
        return None
    return int(value)


def _market_execution(config: dict[str, Any]) -> dict[str, Any]:
    order_type = str(config.get("order_type", "market")).lower()
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
