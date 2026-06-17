# -*- coding: utf-8 -*-
"""
Temporary two-month evaluator for the two AU/AG tick strategy candidates.

It reuses the fast evaluation path from tools.tune_tick_gold_strategies and
does not generate dashboard files, so existing HTML reports are not overwritten.
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.tune_tick_gold_strategies as tuner


START_DATE = "2026-03-15 09:00:00"
END_DATE = "2026-05-15 15:00:00"
OUT_DIR = PROJECT_ROOT / "exports" / "tick_tuning"
OUT_DIR.mkdir(parents=True, exist_ok=True)


VWAP_PARAMS = {
    "strategy": "vwap",
    "lookback_seconds": 240.0,
    "entry_z": 3.0,
    "exit_z": 0.50,
    "min_std_ticks": 6.0,
    "min_deviation_ticks": 10.0,
    "min_ticks_in_window": 80,
    "require_turn_tick": True,
    "turn_ticks": 2.0,
    "hold_seconds": 45.0,
    "take_profit_ticks": 22.0,
    "stop_loss_ticks": 18.0,
    "cooldown_seconds": 60.0,
    "max_spread_ticks": 2.0,
    "max_entries_per_symbol_per_day": 10,
    "enabled_entry_symbols": ["ag"],
    "allowed_entry_hours": [21, 22, 23, 0, 1, 2],
}

BREAKOUT_PARAMS = {
    "strategy": "breakout",
    "breakout_window_seconds": 360.0,
    "breakout_mode": "fade",
    "confirm_window_seconds": 45.0,
    "min_range_ticks": 32.0,
    "breakout_ticks": 1.0,
    "min_directional_ratio": 0.72,
    "min_ticks_in_window": 120,
    "min_tick_volume": 0.0,
    "use_imbalance_filter": False,
    "imbalance_threshold": 0.10,
    "hold_seconds": 45.0,
    "take_profit_ticks": 22.0,
    "stop_loss_ticks": 14.0,
    "cooldown_seconds": 180.0,
    "max_spread_ticks": 2.0,
    "max_entries_per_symbol_per_day": 10,
    "enabled_entry_symbols": ["ag"],
    "allowed_entry_hours": [21, 22, 23, 0, 1, 2],
}


def load_tick_matrix():
    original_start = tuner.START_DATE
    original_end = tuner.END_DATE
    tuner.START_DATE = START_DATE
    tuner.END_DATE = END_DATE
    try:
        return tuner.load_tick_matrix()
    finally:
        tuner.START_DATE = original_start
        tuner.END_DATE = original_end


def main():
    print(f"[Two Month Eval] range={START_DATE} -> {END_DATE}")
    print("[Two Month Eval] loading tick matrix...")
    matrix = load_tick_matrix()
    print(f"[Two Month Eval] matrix shape={matrix.shape}, actual={matrix.index[0]} -> {matrix.index[-1]}")
    print("[Two Month Eval] building events...")
    events = tuner.build_tick_events(matrix)
    trading_days = len({item[0].date() for item in events})
    print(f"[Two Month Eval] events={len(events):,}, trading_days={trading_days}")

    results = []
    for params in (VWAP_PARAMS, BREAKOUT_PARAMS):
        started = time.time()
        row = tuner.evaluate(params, events)
        row["seconds"] = round(time.time() - started, 3)
        row["requested_start"] = START_DATE
        row["requested_end"] = END_DATE
        row["actual_start"] = str(matrix.index[0])
        row["actual_end"] = str(matrix.index[-1])
        row["trading_days"] = trading_days
        row["trades_per_day"] = row["trades"] / trading_days if trading_days else 0.0
        results.append(row)
        print(
            f"[Two Month Eval] {row['strategy']} final={row['final_pnl']:.2f}, "
            f"trades={row['trades']}, trades/day={row['trades_per_day']:.2f}, "
            f"win={row['win_rate']:.2%}, pf={row['profit_factor']:.2f}, "
            f"dd={row['max_trade_drawdown']:.2f}, seconds={row['seconds']}"
        )

    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"tick_gold_two_month_eval_{stamp}.csv"
    fieldnames = sorted({key for row in results for key in row.keys()})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"[Two Month Eval] CSV: {csv_path}")


if __name__ == "__main__":
    main()
