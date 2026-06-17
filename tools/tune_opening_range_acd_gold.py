# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2025-05-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"


BASE_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    "opening_range_bars": 6,
    "atr_period": 48,
    "trend_window": 144,
    "breakout_buffer_ticks": 1.0,
    "min_range_atr": 0.8,
    "max_extension_atr": 1.2,
    "atr_stop_mult": 2.4,
    "trail_atr_mult": 3.2,
    "take_profit_atr": 0.0,
    "exit_on_range_reentry": True,
    "max_hold_bars": 96,
    "cooldown_bars": 12,
    "max_entries_per_symbol_per_day": 2,
    "session_start_hours": "9,21",
    "allowed_entry_hours": "9,10,21,22,23,0,1",
    "sizing": {
        "mode": "equity_pct",
        "value": 0.10,
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


GRID = [
    {"name": "base"},
    {"name": "less_noise", "opening_range_bars": 9, "min_range_atr": 1.0, "cooldown_bars": 24},
    {"name": "wide_confirm", "opening_range_bars": 12, "breakout_buffer_ticks": 2.0, "min_range_atr": 1.0},
    {"name": "trend_slow", "trend_window": 240, "max_extension_atr": 1.5, "atr_stop_mult": 3.0},
    {"name": "night_only", "allowed_entry_hours": "21,22,23,0,1", "opening_range_bars": 6},
    {"name": "day_only", "allowed_entry_hours": "9,10", "opening_range_bars": 6},
    {"name": "hold_longer", "max_hold_bars": 192, "atr_stop_mult": 3.0, "trail_atr_mult": 4.0},
    {"name": "hold_shorter", "max_hold_bars": 48, "atr_stop_mult": 2.0, "trail_atr_mult": 2.8},
    {"name": "tp_3atr", "take_profit_atr": 3.0, "atr_stop_mult": 2.2, "trail_atr_mult": 3.0},
    {"name": "strict_breakout", "breakout_buffer_ticks": 3.0, "max_extension_atr": 1.0, "min_range_atr": 1.2},
    {"name": "loose_range", "opening_range_bars": 3, "min_range_atr": 0.5, "cooldown_bars": 18},
]


def metric_value(metrics: dict, key: str):
    value = metrics.get(key)
    if isinstance(value, str):
        text = value.replace("%", "").replace("¥", "").replace(",", "").strip()
        try:
            return float(text)
        except ValueError:
            return value
    return value


def run_case(params: dict):
    kwargs = dict(BASE_KWARGS)
    kwargs.update({k: v for k, v in params.items() if k != "name"})
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        analyzer = run_backtest(
            strategy_class=OpeningRangeACDStrategy,
            symbols_input=TARGET_SYMBOLS,
            start_date=START_DATE,
            end_date=END_DATE,
            freq="5m",
            data_type="main",
            initial_capital=1_000_000.0,
            strategy_kwargs=kwargs,
            enable_main_rollover=False,
        )
    if analyzer is None:
        return None
    total = analyzer.metrics_list[0]
    return {
        "name": params["name"],
        "return_pct": metric_value(total, "总收益"),
        "sharpe": metric_value(total, "年化Sharpe"),
        "max_dd": metric_value(total, "最大回撤率"),
        "trades": metric_value(total, "交易次数"),
        "win_rate": metric_value(total, "逐笔胜率"),
        "pnl": metric_value(total, "累计盈亏"),
        "params": {k: v for k, v in kwargs.items() if k not in {"target_symbols", "sizing", "execution", "exit", "record_signals"}},
    }


def main():
    results = []
    for params in GRID:
        result = run_case(params)
        if result:
            results.append(result)
            print(
                f"{result['name']:>15} | ret={result['return_pct']:>7.2f}% | "
                f"sharpe={result['sharpe']:>5.2f} | dd={result['max_dd']:>7.2f}% | "
                f"trades={int(result['trades']):>4} | win={result['win_rate']:>6.2f}% | pnl={result['pnl']:,.0f}"
            )

    print("\nTop by return:")
    for item in sorted(results, key=lambda x: (x["return_pct"], x["sharpe"]), reverse=True)[:5]:
        print(item)


if __name__ == "__main__":
    main()
