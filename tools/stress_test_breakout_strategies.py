# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from strategy.custom.donchian_atr_breakout import DonchianATRBreakoutStrategy
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy


INITIAL_CAPITAL = 1_000_000.0
FREQ = "5m"
DATA_TYPE = "main"


UNIVERSES = {
    "au": ["au"],
    "ag": ["ag"],
    "pd": ["pd"],
    "pt": ["pt"],
    "gold": ["au", "ag"],
    "precious": ["au", "ag", "pd", "pt"],
    "metals": ["au", "ag", "cu", "al", "zn", "pb", "ni", "sn"],
    "black": ["rb", "hc", "i", "j", "jm", "sf", "sm", "ss"],
    "energy_chem": ["sc", "bu", "fu", "lu", "pg", "ru", "br", "nr", "eg", "eb", "l", "v", "pp", "ma", "ta"],
}


PERIODS = {
    "6m": ("2025-11-15 09:00:00", "2026-05-15 15:00:00"),
    "1y": ("2025-05-15 09:00:00", "2026-05-15 15:00:00"),
    "2y": ("2024-05-15 09:00:00", "2026-05-15 15:00:00"),
}


COMMON = {
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


STRATEGIES = {
    "donchian_atr": (
        DonchianATRBreakoutStrategy,
        {
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
        },
    ),
    "opening_range_acd": (
        OpeningRangeACDStrategy,
        {
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
        },
    ),
}


def parse_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def extract_total_metrics(analyzer):
    values = list(analyzer.metrics_list[0].values())
    return {
        "return_pct": parse_number(values[1]),
        "annual_return_pct": parse_number(values[2]),
        "mtm_return_pct": parse_number(values[3]),
        "mtm_annual_return_pct": parse_number(values[4]),
        "pnl": parse_number(values[5]),
        "max_open_value": parse_number(values[7]),
        "max_dd_cash": parse_number(values[8]),
        "max_dd_pct": parse_number(values[9]),
        "sharpe": parse_number(values[10]),
        "calmar": parse_number(values[11]),
        "trade_win_rate": parse_number(values[12]),
        "trade_pnl_ratio": parse_number(values[13]),
        "daily_win_rate": parse_number(values[14]),
        "daily_pnl_ratio": parse_number(values[15]),
        "trade_count": int(parse_number(values[16]) or 0),
        "active_trade_days": int(parse_number(values[17]) or 0),
        "market_days": int(parse_number(values[18]) or 0),
        "avg_daily_turnover": parse_number(values[19]),
        "commission": parse_number(values[24]),
    }


def build_kwargs(strategy_key: str, symbols: list[str]) -> dict:
    kwargs = deepcopy(COMMON)
    kwargs.update(STRATEGIES[strategy_key][1])
    kwargs["target_symbols"] = symbols
    return kwargs


def run_case(strategy_key: str, universe_key: str, period_key: str) -> dict:
    strategy_class, _ = STRATEGIES[strategy_key]
    symbols = UNIVERSES[universe_key]
    start_date, end_date = PERIODS[period_key]
    kwargs = build_kwargs(strategy_key, symbols)

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        analyzer = run_backtest(
            strategy_class=strategy_class,
            symbols_input=symbols,
            start_date=start_date,
            end_date=end_date,
            freq=FREQ,
            data_type=DATA_TYPE,
            initial_capital=INITIAL_CAPITAL,
            strategy_kwargs=kwargs,
            enable_main_rollover=False,
        )

    if analyzer is None:
        raise RuntimeError("run_backtest returned None")

    return {
        "strategy": strategy_key,
        "universe": universe_key,
        "symbols": ",".join(symbols),
        "symbol_count": len(symbols),
        "period": period_key,
        "start_date": start_date,
        "end_date": end_date,
        **extract_total_metrics(analyzer),
    }


def case_grid(mode: str):
    if mode == "quick":
        return [
            ("donchian_atr", "gold", "1y"),
            ("opening_range_acd", "gold", "1y"),
            ("donchian_atr", "gold", "2y"),
            ("opening_range_acd", "gold", "2y"),
            ("donchian_atr", "precious", "1y"),
            ("opening_range_acd", "precious", "1y"),
            ("donchian_atr", "metals", "1y"),
            ("opening_range_acd", "metals", "1y"),
            ("donchian_atr", "black", "1y"),
            ("opening_range_acd", "black", "1y"),
        ]
    if mode == "broad":
        return [
            (strategy, universe, period)
            for strategy in STRATEGIES
            for universe in ["gold", "precious", "metals", "black", "energy_chem"]
            for period in ["1y", "2y"]
        ]
    if mode == "singles":
        return [
            (strategy, universe, period)
            for strategy in STRATEGIES
            for universe in ["au", "ag", "pd", "pt"]
            for period in ["1y", "2y"]
        ]
    return [
        (strategy, universe, period)
        for strategy in STRATEGIES
        for universe in UNIVERSES
        for period in PERIODS
    ]


def write_results(rows: list[dict]) -> Path:
    output_dir = PROJECT_ROOT / "exports" / "stress_tests"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"breakout_strategy_stress_{datetime.now():%Y%m%d_%H%M%S}.csv"
    fields = [
        "strategy", "universe", "symbol_count", "symbols", "period", "start_date", "end_date",
        "return_pct", "annual_return_pct", "mtm_return_pct", "mtm_annual_return_pct",
        "pnl", "max_open_value", "max_dd_cash", "max_dd_pct", "sharpe", "calmar",
        "trade_win_rate", "trade_pnl_ratio", "daily_win_rate", "daily_pnl_ratio",
        "trade_count", "active_trade_days", "market_days", "avg_daily_turnover", "commission",
        "status", "error",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Stress-test the currently strongest 5m breakout strategies.")
    parser.add_argument("--mode", choices=["quick", "broad", "singles", "full"], default="quick")
    args = parser.parse_args()

    rows = []
    cases = case_grid(args.mode)
    for idx, (strategy_key, universe_key, period_key) in enumerate(cases, start=1):
        label = f"{strategy_key}/{universe_key}/{period_key}"
        try:
            row = run_case(strategy_key, universe_key, period_key)
            row["status"] = "ok"
            row["error"] = ""
            print(
                f"{idx:02d}/{len(cases):02d} {label:<36} "
                f"ret={row['return_pct']:>8.2f}% sharpe={row['sharpe']:>6.2f} "
                f"dd={row['max_dd_pct']:>8.2f}% trades={row['trade_count']:>5}"
            )
        except Exception as exc:
            row = {
                "strategy": strategy_key,
                "universe": universe_key,
                "symbols": ",".join(UNIVERSES[universe_key]),
                "symbol_count": len(UNIVERSES[universe_key]),
                "period": period_key,
                "start_date": PERIODS[period_key][0],
                "end_date": PERIODS[period_key][1],
                "status": "error",
                "error": str(exc),
            }
            print(f"{idx:02d}/{len(cases):02d} {label:<36} ERROR {exc}")
        rows.append(row)

    output_path = write_results(rows)
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
