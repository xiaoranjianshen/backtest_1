# -*- coding: utf-8 -*-
"""
Temporary tuner for AU/AG tick-level gold strategies.

The script reuses one cached tick matrix, skips HTML rendering, and writes a
ranked CSV/JSON result under exports/tick_tuning. It is intentionally separate
from run_scripts so experiments do not change the normal demo entry points.
"""
from __future__ import annotations

import contextlib
import copy
import csv
import io
import itertools
import json
import random
import sys
import time
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backtest_engine
from analyzer.performance import StrategyAnalyzer
from broker.match_engine import MatchEngine
from config import build_query_symbol
from data_feed.data_provider import DataProvider
from portfolio.account import Account
from strategy.custom.tick_rolling_breakout import TickRollingBreakoutStrategy
from strategy.custom.tick_vwap_reversion import TickVWAPReversionStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2026-05-01 09:00:00"
END_DATE = "2026-05-15 15:00:00"
INITIAL_CAPITAL = 1_000_000.0
OUT_DIR = PROJECT_ROOT / "exports" / "tick_tuning"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_FULL = [9, 10, 13, 14, 21, 22, 23, 0, 1, 2]
ALLOWED_DAY = [9, 10, 13, 14]
ALLOWED_NIGHT = [21, 22, 23, 0, 1, 2]
ALLOWED_OPEN = [9, 21]


def load_tick_matrix():
    query_symbols = [build_query_symbol(sym, "main") for sym in TARGET_SYMBOLS]
    provider = DataProvider()
    return provider.get_history(
        symbols=query_symbols,
        start_date=START_DATE,
        end_date=END_DATE,
        freq="tick",
        data_type="main",
    )


def build_tick_events(df):
    columns_level_1 = list(dict.fromkeys([col[1] for col in df.columns]))
    col_pos = {col: pos for pos, col in enumerate(df.columns)}
    events = []
    for row_tuple in df.itertuples(index=True, name=None):
        bar_data = backtest_engine.extract_tick_bar_data_from_tuple(row_tuple[1:], columns_level_1, col_pos)
        close_prices = backtest_engine._extract_close_prices(bar_data)
        events.append((row_tuple[0], bar_data, close_prices))
    return events


def common_kwargs(params):
    return {
        "target_symbols": TARGET_SYMBOLS,
        "max_spread_ticks": float(params.get("max_spread_ticks", 2.0)),
        "hold_seconds": float(params["hold_seconds"]),
        "take_profit_ticks": float(params["take_profit_ticks"]),
        "stop_loss_ticks": float(params["stop_loss_ticks"]),
        "cooldown_seconds": float(params.get("cooldown_seconds", 30.0)),
        "avoid_session_close_seconds": 180.0,
        "enabled_entry_symbols": params.get("enabled_entry_symbols"),
        "max_entries_per_symbol_per_day": int(params.get("max_entries_per_symbol_per_day", 5)),
        "allowed_entry_hours": params.get("allowed_entry_hours", ALLOWED_FULL),
        "exit_order_type": "opponent",
        "exit_order_ttl_seconds": 2.0,
        "sizing": {
            "mode": "fixed_volume",
            "value": 1,
            "min_volume": 1,
            "max_volume": 1,
            "round_lot": 1,
        },
        "execution": {
            "order_type": params.get("order_type", "opponent"),
            "price_field": params.get("price_field", "last_price"),
            "slippage_ticks": 0.0,
            "order_ttl_seconds": 2.0,
        },
        "exit": {
            "close_pct": 1.0,
            "allow_reverse": False,
            "respect_pending_orders": True,
        },
        "record_signals": False,
    }


def build_strategy_kwargs(strategy_name: str, params: dict):
    kwargs = common_kwargs(params)
    if strategy_name == "vwap":
        kwargs.update({
            "lookback_seconds": float(params["lookback_seconds"]),
            "entry_z": float(params["entry_z"]),
            "exit_z": float(params["exit_z"]),
            "min_std_ticks": float(params["min_std_ticks"]),
            "min_deviation_ticks": float(params["min_deviation_ticks"]),
            "min_ticks_in_window": int(params["min_ticks_in_window"]),
            "require_turn_tick": bool(params["require_turn_tick"]),
            "turn_ticks": float(params["turn_ticks"]),
        })
        return TickVWAPReversionStrategy, kwargs

    if strategy_name == "breakout":
        kwargs.update({
            "breakout_window_seconds": float(params["breakout_window_seconds"]),
            "breakout_mode": params.get("breakout_mode", "follow"),
            "confirm_window_seconds": float(params["confirm_window_seconds"]),
            "min_range_ticks": float(params["min_range_ticks"]),
            "breakout_ticks": float(params["breakout_ticks"]),
            "min_directional_ratio": float(params["min_directional_ratio"]),
            "min_ticks_in_window": int(params["min_ticks_in_window"]),
            "min_tick_volume": float(params["min_tick_volume"]),
            "use_imbalance_filter": bool(params["use_imbalance_filter"]),
            "imbalance_threshold": float(params["imbalance_threshold"]),
        })
        return TickRollingBreakoutStrategy, kwargs

    raise ValueError(f"Unknown strategy_name: {strategy_name}")


def vwap_candidates():
    grid = {
        "lookback_seconds": [120.0, 240.0, 420.0],
        "entry_z": [2.4, 2.8, 3.2, 3.8],
        "exit_z": [0.15, 0.30, 0.50],
        "min_std_ticks": [2.0, 4.0, 6.0],
        "min_deviation_ticks": [8.0, 12.0, 18.0],
        "min_ticks_in_window": [80, 160],
        "require_turn_tick": [True],
        "turn_ticks": [1.0, 2.0],
        "hold_seconds": [20.0, 45.0, 90.0],
        "take_profit_ticks": [12.0, 18.0, 26.0],
        "stop_loss_ticks": [8.0, 12.0, 18.0],
        "cooldown_seconds": [30.0, 90.0, 180.0],
        "max_spread_ticks": [1.0, 2.0],
        "max_entries_per_symbol_per_day": [3, 5],
        "enabled_entry_symbols": [None, ["ag"]],
        "allowed_entry_hours": [ALLOWED_FULL, ALLOWED_DAY, ALLOWED_NIGHT, ALLOWED_OPEN],
    }
    keys = list(grid)
    for values in itertools.product(*[grid[key] for key in keys]):
        yield {"strategy": "vwap", **dict(zip(keys, values))}


def breakout_candidates():
    grid = {
        "breakout_window_seconds": [90.0, 180.0, 360.0],
        "breakout_mode": ["follow", "fade"],
        "confirm_window_seconds": [8.0, 20.0, 45.0],
        "min_range_ticks": [12.0, 20.0, 32.0],
        "breakout_ticks": [0.0, 1.0, 2.0],
        "min_directional_ratio": [0.62, 0.72, 0.82],
        "min_ticks_in_window": [60, 120],
        "min_tick_volume": [0.0],
        "use_imbalance_filter": [False, True],
        "imbalance_threshold": [0.10, 0.25],
        "hold_seconds": [15.0, 45.0, 90.0],
        "take_profit_ticks": [14.0, 22.0, 34.0],
        "stop_loss_ticks": [8.0, 14.0, 22.0],
        "cooldown_seconds": [60.0, 180.0, 300.0],
        "max_spread_ticks": [1.0, 2.0],
        "max_entries_per_symbol_per_day": [3, 5],
        "enabled_entry_symbols": [None, ["ag"]],
        "allowed_entry_hours": [ALLOWED_FULL, ALLOWED_DAY, ALLOWED_NIGHT, ALLOWED_OPEN],
    }
    keys = list(grid)
    for values in itertools.product(*[grid[key] for key in keys]):
        yield {"strategy": "breakout", **dict(zip(keys, values))}


def select_candidates(max_runs: int):
    rng = random.Random(20260615)
    seed_candidates = [
        {
            "strategy": "vwap",
            "lookback_seconds": 240.0,
            "entry_z": 3.2,
            "exit_z": 0.30,
            "min_std_ticks": 4.0,
            "min_deviation_ticks": 12.0,
            "min_ticks_in_window": 160,
            "require_turn_tick": True,
            "turn_ticks": 1.0,
            "hold_seconds": 45.0,
            "take_profit_ticks": 18.0,
            "stop_loss_ticks": 12.0,
            "cooldown_seconds": 90.0,
            "max_spread_ticks": 2.0,
            "max_entries_per_symbol_per_day": 5,
            "enabled_entry_symbols": None,
            "allowed_entry_hours": ALLOWED_FULL,
        },
        {
            "strategy": "breakout",
            "breakout_window_seconds": 180.0,
            "breakout_mode": "follow",
            "confirm_window_seconds": 20.0,
            "min_range_ticks": 20.0,
            "breakout_ticks": 1.0,
            "min_directional_ratio": 0.72,
            "min_ticks_in_window": 120,
            "min_tick_volume": 0.0,
            "use_imbalance_filter": True,
            "imbalance_threshold": 0.10,
            "hold_seconds": 45.0,
            "take_profit_ticks": 22.0,
            "stop_loss_ticks": 14.0,
            "cooldown_seconds": 180.0,
            "max_spread_ticks": 2.0,
            "max_entries_per_symbol_per_day": 5,
            "enabled_entry_symbols": None,
            "allowed_entry_hours": ALLOWED_FULL,
        },
    ]
    focused = focused_vwap_candidates() + focused_breakout_candidates()
    vwap_pool = list(vwap_candidates())
    breakout_pool = list(breakout_candidates())
    remaining = max(0, max_runs - len(seed_candidates) - len(focused))
    vwap_n = remaining // 2
    breakout_n = remaining - vwap_n
    return (
        seed_candidates
        + focused[:max(0, max_runs - len(seed_candidates))]
        + rng.sample(vwap_pool, min(vwap_n, len(vwap_pool)))
        + rng.sample(breakout_pool, min(breakout_n, len(breakout_pool)))
    )[:max_runs]


def focused_vwap_candidates():
    base = {
        "strategy": "vwap",
        "lookback_seconds": 240.0,
        "entry_z": 3.2,
        "exit_z": 0.50,
        "min_std_ticks": 6.0,
        "min_deviation_ticks": 8.0,
        "min_ticks_in_window": 80,
        "require_turn_tick": True,
        "turn_ticks": 2.0,
        "hold_seconds": 45.0,
        "take_profit_ticks": 12.0,
        "stop_loss_ticks": 18.0,
        "cooldown_seconds": 180.0,
        "max_spread_ticks": 2.0,
        "max_entries_per_symbol_per_day": 3,
        "enabled_entry_symbols": None,
        "allowed_entry_hours": ALLOWED_NIGHT,
    }

    variations = []
    for max_entries, cooldown, entry_z, min_deviation, take_profit, stop_loss in [
        (10, 30.0, 3.0, 10.0, 18.0, 18.0),
        (12, 30.0, 3.0, 10.0, 18.0, 18.0),
        (15, 30.0, 3.0, 10.0, 18.0, 18.0),
        (12, 45.0, 3.0, 9.0, 18.0, 18.0),
        (12, 60.0, 2.8, 10.0, 18.0, 18.0),
        (10, 60.0, 3.0, 10.0, 22.0, 18.0),
    ]:
        item = dict(base)
        item.update({
            "enabled_entry_symbols": ["ag"],
            "max_entries_per_symbol_per_day": max_entries,
            "cooldown_seconds": cooldown,
            "entry_z": entry_z,
            "min_deviation_ticks": min_deviation,
            "take_profit_ticks": take_profit,
            "stop_loss_ticks": stop_loss,
        })
        variations.append(item)

    for enabled_symbols, max_entries, cooldown in [
        (["ag"], 5, 120.0),
        (["ag"], 8, 90.0),
        (["ag"], 10, 60.0),
        (["ag"], 10, 120.0),
        (None, 5, 120.0),
        (None, 8, 90.0),
    ]:
        item = dict(base)
        item.update({
            "enabled_entry_symbols": enabled_symbols,
            "max_entries_per_symbol_per_day": max_entries,
            "cooldown_seconds": cooldown,
        })
        variations.append(item)

    for entry_z, min_deviation, take_profit, stop_loss in [
        (3.0, 8.0, 12.0, 18.0),
        (3.0, 10.0, 18.0, 18.0),
        (3.4, 8.0, 12.0, 18.0),
        (3.4, 12.0, 18.0, 24.0),
        (3.8, 12.0, 18.0, 24.0),
        (2.8, 8.0, 12.0, 18.0),
    ]:
        item = dict(base)
        item.update({
            "enabled_entry_symbols": ["ag"],
            "max_entries_per_symbol_per_day": 10,
            "cooldown_seconds": 60.0,
            "entry_z": entry_z,
            "min_deviation_ticks": min_deviation,
            "take_profit_ticks": take_profit,
            "stop_loss_ticks": stop_loss,
        })
        variations.append(item)

    for allowed_hours in [ALLOWED_FULL, ALLOWED_DAY, ALLOWED_OPEN]:
        item = dict(base)
        item.update({
            "enabled_entry_symbols": ["ag"],
            "max_entries_per_symbol_per_day": 10,
            "cooldown_seconds": 60.0,
            "allowed_entry_hours": allowed_hours,
        })
        variations.append(item)

    return variations


def focused_breakout_candidates():
    base = {
        "strategy": "breakout",
        "breakout_window_seconds": 180.0,
        "breakout_mode": "fade",
        "confirm_window_seconds": 20.0,
        "min_range_ticks": 20.0,
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
        "allowed_entry_hours": ALLOWED_NIGHT,
    }
    variations = []
    for window_seconds, confirm_seconds, min_range, directional_ratio in [
        (90.0, 8.0, 12.0, 0.62),
        (180.0, 20.0, 20.0, 0.72),
        (360.0, 45.0, 32.0, 0.72),
        (180.0, 45.0, 20.0, 0.82),
    ]:
        item = dict(base)
        item.update({
            "breakout_window_seconds": window_seconds,
            "confirm_window_seconds": confirm_seconds,
            "min_range_ticks": min_range,
            "min_directional_ratio": directional_ratio,
        })
        variations.append(item)
    for take_profit, stop_loss, hold_seconds in [
        (14.0, 8.0, 15.0),
        (22.0, 14.0, 45.0),
        (34.0, 22.0, 90.0),
    ]:
        item = dict(base)
        item.update({
            "take_profit_ticks": take_profit,
            "stop_loss_ticks": stop_loss,
            "hold_seconds": hold_seconds,
        })
        variations.append(item)
    return variations


def evaluate(params: dict, events: list):
    strategy_name = params["strategy"]
    strategy_class, kwargs = build_strategy_kwargs(strategy_name, params)
    with contextlib.redirect_stdout(io.StringIO()):
        account = Account(initial_capital=INITIAL_CAPITAL)
        broker = MatchEngine(account=account)
        strategy = strategy_class(
            broker=broker,
            account=account,
            symbol="multi",
            **kwargs,
        )
        strategy.on_init()

        last_date = None
        last_close_prices = {}
        for current_time, bar_data, close_prices in events:
            current_date = current_time.date()
            if last_date is not None and current_date != last_date:
                account.settle_daily()
            last_date = current_date

            broker.process_cross_section(current_time, bar_data)
            strategy.on_bar(current_time, bar_data)
            if close_prices:
                last_close_prices = close_prices

        final_equity = (
            account.get_total_equity(last_close_prices)
            if last_close_prices else account.available + account.frozen_margin
        )
        analyzer = StrategyAnalyzer(
            trades=broker.trade_history,
            price_df=pd.DataFrame(),
            initial_capital=INITIAL_CAPITAL,
            symbol="MULTI",
            freq="tick",
            strategy_name=strategy_class.__name__,
            account_summary={
                "final_equity": final_equity,
                "available": account.available,
                "frozen_margin": account.frozen_margin,
                "rollover_count": 0,
                "rollover_commission": 0.0,
            },
        )
        analyzer._match_trades_fifo()

    row = copy.deepcopy(params)
    row["strategy_class"] = strategy_class.__name__
    row["final_equity"] = float(final_equity)
    row["final_pnl"] = float(final_equity) - INITIAL_CAPITAL
    row["frozen_margin"] = float(account.frozen_margin)

    if analyzer.match_df is None or analyzer.match_df.empty:
        row.update({
            "trades": 0,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "commission": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_trade_drawdown": 0.0,
            "max_hold_seconds": 0.0,
            "avg_hold_seconds": 0.0,
            "au_pnl": 0.0,
            "ag_pnl": 0.0,
        })
        return row

    df = analyzer.match_df.copy()
    wins = df[df["net_pnl"] > 0]["net_pnl"]
    losses = df[df["net_pnl"] <= 0]["net_pnl"]
    curve = df["net_pnl"].cumsum()
    drawdown = curve - curve.cummax()
    hold_seconds = (
        pd.to_datetime(df["close_time"]) - pd.to_datetime(df["open_time"])
    ).dt.total_seconds() if {"open_time", "close_time"}.issubset(df.columns) else pd.Series(dtype=float)
    by_symbol = df.groupby("symbol")["net_pnl"].sum().to_dict()

    row.update({
        "trades": int(len(df)),
        "net_pnl": float(df["net_pnl"].sum()),
        "gross_pnl": float(df["gross_pnl"].sum()) if "gross_pnl" in df else 0.0,
        "commission": float(df["commission"].sum()) if "commission" in df else 0.0,
        "win_rate": float(len(wins) / len(df)) if len(df) else 0.0,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else 0.0,
        "max_trade_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "max_hold_seconds": float(hold_seconds.max()) if len(hold_seconds) else 0.0,
        "avg_hold_seconds": float(hold_seconds.mean()) if len(hold_seconds) else 0.0,
        "au_pnl": float(by_symbol.get("au", 0.0)),
        "ag_pnl": float(by_symbol.get("ag", 0.0)),
    })
    return row


def score(row: dict, trading_days: int) -> float:
    if row.get("frozen_margin", 0.0) > 0:
        return -1e18
    if row.get("max_hold_seconds", 0.0) > float(row.get("hold_seconds", 0.0)) + 5.0:
        return -1e18

    desired_trades = max(1, trading_days * 10)
    trades = float(row.get("trades", 0))
    win_rate = float(row.get("win_rate", 0.0))
    final_pnl = float(row.get("final_pnl", 0.0))
    drawdown = abs(float(row.get("max_trade_drawdown", 0.0)))
    trade_fit = max(0.0, 1.0 - abs(trades - desired_trades) / desired_trades)
    if trades < max(10, desired_trades * 0.25):
        trade_fit *= 0.25
    if trades > desired_trades * 2.5:
        trade_fit *= 0.25

    return final_pnl + 2500.0 * win_rate + 2000.0 * trade_fit - 0.20 * drawdown


def main(max_runs: int = 24, strategy_filter: str | None = None):
    print("[Gold Tuner] Loading tick matrix once...")
    df = load_tick_matrix()
    print(f"[Gold Tuner] Matrix shape={df.shape}, range={df.index[0]} -> {df.index[-1]}")
    print("[Gold Tuner] Building reusable tick events...")
    events = build_tick_events(df)
    trading_days = len({item[0].date() for item in events})
    print(f"[Gold Tuner] Events={len(events):,}, trading_days={trading_days}")

    selected = select_candidates(max_runs=max_runs)
    if strategy_filter:
        strategy_filter = strategy_filter.strip().lower()
        selected = [params for params in selected if params["strategy"] == strategy_filter]
        print(f"[Gold Tuner] Strategy filter={strategy_filter}, selected={len(selected)}")
    results = []
    started = time.time()
    best = None
    for idx, params in enumerate(selected, start=1):
        row_start = time.time()
        row = evaluate(params, events)
        row["score"] = score(row, trading_days)
        row["seconds"] = round(time.time() - row_start, 3)
        results.append(row)
        if best is None or row["score"] > best["score"]:
            best = row
        print(
            f"[Gold Tuner] {idx:03d}/{len(selected)} {row['strategy']} "
            f"final={row['final_pnl']:.2f} trades={row['trades']} "
            f"wr={row['win_rate']:.2%} pf={row['profit_factor']:.2f} "
            f"best={best['strategy']}:{best['final_pnl']:.2f}/{best['trades']}"
        )

    results = sorted(results, key=lambda row: row["score"], reverse=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"tick_gold_strategy_tuning_{stamp}.csv"
    json_path = OUT_DIR / f"tick_gold_strategy_best_{stamp}.json"
    fieldnames = sorted({key for row in results for key in row.keys()})

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(results[:10], f, ensure_ascii=False, indent=2)

    print(f"[Gold Tuner] Finished in {(time.time() - started):.1f}s")
    print(f"[Gold Tuner] CSV: {csv_path}")
    print(f"[Gold Tuner] Best JSON: {json_path}")
    print("[Gold Tuner] Top 10:")
    for rank, row in enumerate(results[:10], start=1):
        print(
            f"#{rank} {row['strategy']} final={row['final_pnl']:.2f}, "
            f"net={row['net_pnl']:.2f}, trades={row['trades']}, "
            f"wr={row['win_rate']:.2%}, pf={row['profit_factor']:.2f}, "
            f"dd={row['max_trade_drawdown']:.2f}, au={row['au_pnl']:.2f}, ag={row['ag_pnl']:.2f}"
        )


if __name__ == "__main__":
    max_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    strategy_filter = sys.argv[2] if len(sys.argv) > 2 else None
    main(max_runs=max_runs, strategy_filter=strategy_filter)
