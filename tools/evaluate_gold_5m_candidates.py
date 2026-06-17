# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import re
import sys
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from strategy.custom.donchian_atr_breakout import DonchianATRBreakoutStrategy
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy
from strategy.custom.utbot_stc_hull import UTBotSTCHullStrategy
from strategy.custom.vwap_band_reversion import VWAPBandReversionStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2025-05-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"

COMMON = {
    "target_symbols": TARGET_SYMBOLS,
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


def merged(extra: dict) -> dict:
    base = deepcopy(COMMON)
    base.update(extra)
    return base


CANDIDATES = [
    (
        "donchian_atr_reference",
        DonchianATRBreakoutStrategy,
        merged({
            "donchian_window": 144,
            "atr_period": 72,
            "trend_window": 240,
            "breakout_buffer_ticks": 2.0,
            "min_channel_atr": 1.8,
            "max_extension_atr": 1.5,
            "atr_stop_mult": 3.2,
            "max_hold_bars": 384,
            "cooldown_bars": 18,
            "max_entries_per_symbol_per_day": 3,
            "allowed_entry_hours": "9,10,13,21,22,23,0,1,2",
        }),
    ),
    (
        "opening_range_acd_tuned",
        OpeningRangeACDStrategy,
        merged({
            "opening_range_bars": 3,
            "atr_period": 48,
            "trend_window": 144,
            "breakout_buffer_ticks": 1.0,
            "min_range_atr": 0.5,
            "max_extension_atr": 1.2,
            "atr_stop_mult": 3.0,
            "trail_atr_mult": 4.0,
            "take_profit_atr": 0.0,
            "exit_on_range_reentry": False,
            "max_hold_bars": 384,
            "cooldown_bars": 18,
            "max_entries_per_symbol_per_day": 2,
            "session_start_hours": "9,21",
            "allowed_entry_hours": "9,10,21,22,23,0,1",
        }),
    ),
    (
        "vwap_reversion_base",
        VWAPBandReversionStrategy,
        merged({
            "std_window": 48,
            "entry_z": 2.0,
            "exit_z": 0.25,
            "min_bars_in_session": 12,
            "min_std_ticks": 4.0,
            "max_vwap_slope_ticks": 6.0,
            "slope_window": 6,
            "take_profit_ticks": 28.0,
            "stop_loss_ticks": 18.0,
            "max_hold_bars": 24,
            "cooldown_bars": 3,
            "max_entries_per_symbol_per_day": 20,
            "session_start_hour": 21,
        }),
    ),
    (
        "vwap_reversion_wide",
        VWAPBandReversionStrategy,
        merged({
            "std_window": 72,
            "entry_z": 2.5,
            "exit_z": 0.10,
            "min_bars_in_session": 18,
            "min_std_ticks": 6.0,
            "max_vwap_slope_ticks": 4.0,
            "slope_window": 8,
            "take_profit_ticks": 40.0,
            "stop_loss_ticks": 24.0,
            "max_hold_bars": 36,
            "cooldown_bars": 6,
            "max_entries_per_symbol_per_day": 10,
            "session_start_hour": 21,
        }),
    ),
    (
        "utbot_stc_hull_base",
        UTBotSTCHullStrategy,
        merged({
            "hma_length": 55,
            "atr_period": 10,
            "ut_key_value": 1.0,
            "stc_length": 12,
            "stc_fast": 26,
            "stc_slow": 50,
            "stc_factor": 0.5,
            "stc_long_max": 35.0,
            "stc_short_min": 65.0,
            "require_price_above_hull": True,
            "exit_on_opposite_signal": True,
            "take_profit_ticks": 24.0,
            "stop_loss_ticks": 16.0,
            "max_hold_bars": 36,
            "cooldown_bars": 3,
            "max_entries_per_symbol_per_day": 20,
        }),
    ),
    (
        "utbot_stc_hull_slow",
        UTBotSTCHullStrategy,
        merged({
            "hma_length": 89,
            "atr_period": 14,
            "ut_key_value": 1.6,
            "stc_length": 14,
            "stc_fast": 23,
            "stc_slow": 50,
            "stc_factor": 0.5,
            "stc_long_max": 45.0,
            "stc_short_min": 55.0,
            "require_price_above_hull": True,
            "exit_on_opposite_signal": True,
            "take_profit_ticks": 48.0,
            "stop_loss_ticks": 28.0,
            "max_hold_bars": 72,
            "cooldown_bars": 6,
            "max_entries_per_symbol_per_day": 10,
        }),
    ),
]


def parse_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    text = text.replace(",", "")
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


def run_case(name, strategy_class, kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        analyzer = run_backtest(
            strategy_class=strategy_class,
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
    metrics = extract_total_metrics(analyzer)
    metrics["name"] = name
    return metrics


def main():
    results = []
    for name, strategy_class, kwargs in CANDIDATES:
        result = run_case(name, strategy_class, kwargs)
        if result is None:
            print(f"{name:>28} | failed")
            continue
        results.append(result)
        print(
            f"{name:>28} | ret={result['return_pct']:>7.2f}% | "
            f"sharpe={result['sharpe']:>5.2f} | calmar={result['calmar']:>5.2f} | "
            f"dd={result['max_dd']:>7.2f}% | trades={result['trades']:>4} | "
            f"win={result['win_rate']:>6.2f}% | pl={result['pnl_ratio']:>4.2f} | "
            f"pnl={result['pnl']:>10,.0f} | fee={result['commission']:>8,.0f}"
        )

    print("\nTop candidates:")
    for item in sorted(results, key=lambda x: (x["return_pct"], x["sharpe"]), reverse=True):
        print(item)


if __name__ == "__main__":
    main()
