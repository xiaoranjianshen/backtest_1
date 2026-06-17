# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import re
import sys
from copy import deepcopy
from itertools import product
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
    "opening_range_bars": 3,
    "atr_period": 48,
    "trend_window": 144,
    "breakout_buffer_ticks": 1.0,
    "min_range_atr": 0.5,
    "max_extension_atr": 1.2,
    "atr_stop_mult": 2.4,
    "trail_atr_mult": 3.2,
    "take_profit_atr": 0.0,
    "exit_on_range_reentry": True,
    "max_hold_bars": 96,
    "cooldown_bars": 18,
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


def parse_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else value


def extract_total_metrics(analyzer):
    values = list(analyzer.metrics_list[0].values())
    return {
        "return_pct": parse_number(values[1]),
        "sharpe": parse_number(values[10]),
        "calmar": parse_number(values[11]),
        "max_dd": parse_number(values[9]),
        "trades": int(parse_number(values[16])),
        "win_rate": parse_number(values[12]),
        "pnl_ratio": parse_number(values[13]),
        "pnl": parse_number(values[5]),
        "commission": parse_number(values[24]),
    }


def run_case(params: dict):
    kwargs = deepcopy(BASE_KWARGS)
    kwargs.update(params)
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
    result = extract_total_metrics(analyzer)
    result["params"] = params
    return result


def main():
    cases = []
    for opening_range_bars, min_range_atr, max_extension_atr, stop_pair in product(
        [2, 3, 4, 5],
        [0.3, 0.5, 0.8],
        [1.0, 1.2, 1.5],
        [(2.0, 2.8), (2.4, 3.2), (3.0, 4.0)],
    ):
        atr_stop_mult, trail_atr_mult = stop_pair
        cases.append({
            "opening_range_bars": opening_range_bars,
            "min_range_atr": min_range_atr,
            "max_extension_atr": max_extension_atr,
            "atr_stop_mult": atr_stop_mult,
            "trail_atr_mult": trail_atr_mult,
        })

    extra_cases = [
        {"max_hold_bars": 192, "atr_stop_mult": 3.0, "trail_atr_mult": 4.0},
        {"max_hold_bars": 384, "exit_on_range_reentry": False, "atr_stop_mult": 3.0, "trail_atr_mult": 4.0},
        {"cooldown_bars": 6, "max_entries_per_symbol_per_day": 3},
        {"allowed_entry_hours": "9,10,11,13,14,21,22,23,0,1,2"},
        {"opening_range_bars": 2, "min_range_atr": 0.3, "max_hold_bars": 192, "exit_on_range_reentry": False},
        {"opening_range_bars": 4, "min_range_atr": 0.5, "max_hold_bars": 192, "atr_stop_mult": 3.0, "trail_atr_mult": 4.0},
    ]
    cases.extend(extra_cases)

    results = []
    total = len(cases)
    for idx, params in enumerate(cases, start=1):
        result = run_case(params)
        if result is None:
            print(f"{idx:03d}/{total} failed {params}")
            continue
        results.append(result)
        print(
            f"{idx:03d}/{total} ret={result['return_pct']:>7.2f}% "
            f"sharpe={result['sharpe']:>5.2f} dd={result['max_dd']:>7.2f}% "
            f"trades={result['trades']:>4} win={result['win_rate']:>6.2f}% params={params}"
        )

    print("\nTop by return:")
    for item in sorted(results, key=lambda x: (x["return_pct"], x["sharpe"]), reverse=True)[:12]:
        print(item)


if __name__ == "__main__":
    main()
