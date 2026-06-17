# -*- coding: utf-8 -*-
"""
Batch-tune TickAnomalyScalpingStrategy on the same cached tick matrix.

This utility is intentionally separate from the demo runner. It loads the data
once, reuses it for all parameter candidates, and skips HTML rendering so tick
experiments remain practical.
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
from run_scripts.run_tick_anomaly_scalping_gold import (
    END_DATE,
    START_DATE,
    STRATEGY_KWARGS,
    TARGET_SYMBOLS,
)
from strategy.custom.tick_anomaly_scalping import TickAnomalyScalpingStrategy


OUT_DIR = PROJECT_ROOT / "exports" / "tick_tuning"
OUT_DIR.mkdir(parents=True, exist_ok=True)


class CachedDataProvider:
    def __init__(self, df):
        self.df = df

    def get_history(self, symbols, start_date, end_date, freq, data_type):
        return self.df


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


def candidate_grid():
    common = {
        "scalp_mode": ["reversal"],
        "shock_window_seconds": [2.0, 3.0, 5.0, 8.0],
        "tail_prob": [0.002, 0.005, 0.01, 0.02],
        "min_move_bps": [1.0, 1.5, 2.0, 3.0],
        "directional_ratio": [0.55, 0.60, 0.65, 0.70],
        "max_spread_ticks": [1.0, 2.0],
        "hold_seconds": [3.0, 5.0, 8.0, 12.0],
        "take_profit_ticks": [3.0, 4.0, 6.0, 8.0, 10.0],
        "stop_loss_ticks": [4.0, 6.0, 8.0, 10.0],
        "reversal_confirm_seconds": [1.0, 1.5, 2.0, 2.5],
        "reversal_retrace_ratio": [0.25, 0.35, 0.50, 0.65],
        "avoid_session_close_seconds": [90.0],
    }
    execution = {
        "order_type": ["opponent", "limit"],
        "price_field": ["mid_price"],
        "limit_mode": ["at_close", "better_ticks", "worse_ticks"],
        "limit_ticks": [0.0, 1.0],
    }

    keys = list(common) + list(execution)
    value_grid = [common.get(key, execution.get(key)) for key in keys]
    for values in itertools.product(*value_grid):
        params = dict(zip(keys, values))
        if params["order_type"] == "opponent":
            if params["limit_mode"] != "at_close" or params["limit_ticks"] != 0.0:
                continue
        yield params


def focused_candidates():
    base = {
        "scalp_mode": "reversal",
        "shock_window_seconds": 5.0,
        "tail_prob": 0.02,
        "min_move_bps": 1.5,
        "directional_ratio": 0.55,
        "max_spread_ticks": 2.0,
        "hold_seconds": 3.0,
        "reversal_confirm_seconds": 1.0,
        "reversal_retrace_ratio": 0.50,
        "avoid_session_close_seconds": 90.0,
        "order_type": "limit",
        "price_field": "mid_price",
        "limit_mode": "at_close",
        "limit_ticks": 0.0,
    }

    candidates = []
    for take_profit, stop_loss, hold_seconds in [
        (6.0, 4.0, 3.0),
        (8.0, 4.0, 3.0),
        (10.0, 4.0, 3.0),
        (6.0, 6.0, 3.0),
        (8.0, 6.0, 3.0),
        (10.0, 6.0, 3.0),
        (4.0, 8.0, 5.0),
        (6.0, 8.0, 5.0),
        (8.0, 8.0, 5.0),
    ]:
        item = dict(base)
        item.update({
            "take_profit_ticks": take_profit,
            "stop_loss_ticks": stop_loss,
            "hold_seconds": hold_seconds,
        })
        candidates.append(item)

    for order_type, limit_mode, limit_ticks in [
        ("opponent", "at_close", 0.0),
        ("limit", "better_ticks", 1.0),
        ("limit", "worse_ticks", 0.0),
    ]:
        item = dict(base)
        item.update({
            "take_profit_ticks": 10.0,
            "stop_loss_ticks": 4.0,
            "order_type": order_type,
            "limit_mode": limit_mode,
            "limit_ticks": limit_ticks,
        })
        candidates.append(item)

    return candidates


def build_kwargs(params):
    execution_keys = {"order_type", "price_field", "limit_mode", "limit_ticks"}
    strategy_params = {key: value for key, value in params.items() if key not in execution_keys}

    kwargs = copy.deepcopy(STRATEGY_KWARGS)
    kwargs.update(strategy_params)
    kwargs.update({
        "target_symbols": TARGET_SYMBOLS,
        "min_history_samples": int(params.get("min_history_samples", kwargs.get("min_history_samples", 1000))),
        "threshold_refresh_ticks": 50,
        "pause_seconds": 1.0,
        "reversal_min_retrace_ticks": 2.0,
        "avoid_session_close_seconds": float(params.get("avoid_session_close_seconds", 90.0)),
        "require_history_ready": True,
        "warmup_days": 0.25,
        "record_signals": False,
    })
    kwargs["exit_order_type"] = "opponent"
    kwargs["exit_order_ttl_seconds"] = 2.0
    kwargs["sizing"] = {
        "mode": "fixed_volume",
        "value": 1,
        "min_volume": 1,
        "max_volume": 1,
        "round_lot": 1,
    }
    kwargs["execution"] = {
        "order_type": params.get("order_type", "opponent"),
        "price_field": params.get("price_field", "mid_price"),
        "slippage_ticks": 0.0,
        "order_ttl_seconds": 2.0,
    }
    if kwargs["execution"]["order_type"] == "limit":
        kwargs["execution"].update({
            "limit_mode": params.get("limit_mode", "at_close"),
            "ticks": float(params.get("limit_ticks", 0.0)),
        })
    kwargs["exit"] = {
        "close_pct": 1.0,
        "allow_reverse": False,
        "respect_pending_orders": True,
    }
    return kwargs


def evaluate(params, events):
    kwargs = build_kwargs(params)
    with contextlib.redirect_stdout(io.StringIO()):
        account = Account(initial_capital=1_000_000.0)
        broker = MatchEngine(account=account)
        strategy = TickAnomalyScalpingStrategy(
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
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name=TickAnomalyScalpingStrategy.__name__,
            account_summary={
                "final_equity": final_equity,
                "available": account.available,
                "frozen_margin": account.frozen_margin,
                "rollover_count": 0,
                "rollover_commission": 0.0,
            },
        )
        analyzer._match_trades_fifo()

    if analyzer is None or analyzer.match_df is None or analyzer.match_df.empty:
        return {
            **params,
            "net_pnl": 0.0,
            "final_pnl": 0.0,
            "gross_pnl": 0.0,
            "commission": 0.0,
            "frozen_margin": 0.0,
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_trade_drawdown": 0.0,
        }

    df = analyzer.match_df.copy()
    net_pnl = float(df["net_pnl"].sum())
    account_summary = analyzer.account_summary or {}
    final_pnl = float(account_summary.get("final_equity", 1_000_000.0)) - 1_000_000.0
    frozen_margin = float(account_summary.get("frozen_margin", 0.0))
    gross_pnl = float(df["gross_pnl"].sum()) if "gross_pnl" in df else 0.0
    commission = float(df["commission"].sum()) if "commission" in df else 0.0
    trades = int(len(df))
    wins = df[df["net_pnl"] > 0]["net_pnl"]
    losses = df[df["net_pnl"] <= 0]["net_pnl"]
    win_rate = float(len(wins) / trades) if trades else 0.0
    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else 0.0
    curve = df["net_pnl"].cumsum()
    drawdown = curve - curve.cummax()

    hold_seconds = (
        pd.to_datetime(df["close_time"]) - pd.to_datetime(df["open_time"])
    ).dt.total_seconds() if {"open_time", "close_time"}.issubset(df.columns) else pd.Series(dtype=float)
    by_symbol = df.groupby("symbol")["net_pnl"].sum().to_dict()
    return {
        **params,
        "net_pnl": net_pnl,
        "final_pnl": final_pnl,
        "gross_pnl": gross_pnl,
        "commission": commission,
        "frozen_margin": frozen_margin,
        "trades": trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_trade_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "au_pnl": float(by_symbol.get("au", 0.0)),
        "ag_pnl": float(by_symbol.get("ag", 0.0)),
        "max_hold_seconds": float(hold_seconds.max()) if len(hold_seconds) else 0.0,
        "avg_hold_seconds": float(hold_seconds.mean()) if len(hold_seconds) else 0.0,
    }


def score(row):
    if row["trades"] < 10:
        return -1e18
    if row.get("frozen_margin", 0.0) > 0:
        return -1e18
    if row.get("max_hold_seconds", 0.0) > row.get("hold_seconds", 0.0) + 5.0:
        return -1e18

    trade_bonus = min(float(row["trades"]), 40.0) / 40.0
    win_component = 5000.0 * float(row["win_rate"])
    count_component = 1200.0 * trade_bonus
    pnl_component = 0.5 * float(row["final_pnl"])
    drawdown_penalty = 0.25 * abs(float(row["max_trade_drawdown"]))
    return win_component + count_component + pnl_component - drawdown_penalty


def main(max_runs: int = 120):
    print("[Tuner] Loading tick matrix once...")
    df = load_tick_matrix()
    print(f"[Tuner] Matrix shape={df.shape}, range={df.index[0]} -> {df.index[-1]}")
    print("[Tuner] Building reusable tick events...")
    events = build_tick_events(df)
    print(f"[Tuner] Events={len(events):,}")

    all_candidates = list(candidate_grid())
    # Deterministic coverage: test the current script first, then sample the
    # grid with a fixed seed so repeated tuning runs are comparable.
    current = {
        "scalp_mode": STRATEGY_KWARGS["scalp_mode"],
        "shock_window_seconds": STRATEGY_KWARGS["shock_window_seconds"],
        "tail_prob": STRATEGY_KWARGS["tail_prob"],
        "min_move_bps": STRATEGY_KWARGS["min_move_bps"],
        "directional_ratio": STRATEGY_KWARGS["directional_ratio"],
        "max_spread_ticks": STRATEGY_KWARGS["max_spread_ticks"],
        "hold_seconds": STRATEGY_KWARGS["hold_seconds"],
        "take_profit_ticks": STRATEGY_KWARGS["take_profit_ticks"],
        "stop_loss_ticks": STRATEGY_KWARGS["stop_loss_ticks"],
        "reversal_confirm_seconds": STRATEGY_KWARGS["reversal_confirm_seconds"],
        "reversal_retrace_ratio": STRATEGY_KWARGS["reversal_retrace_ratio"],
        "order_type": STRATEGY_KWARGS["execution"]["order_type"],
        "price_field": STRATEGY_KWARGS["execution"]["price_field"],
        "limit_mode": STRATEGY_KWARGS["execution"].get("limit_mode", "at_close"),
        "limit_ticks": STRATEGY_KWARGS["execution"].get("ticks", 0.0),
    }
    rng = random.Random(20260615)
    focused = focused_candidates()
    remaining_slots = max(0, max_runs - 1 - len(focused))
    sample_size = min(remaining_slots, len(all_candidates))
    selected = [current] + focused[:max(0, max_runs - 1)] + rng.sample(all_candidates, sample_size)
    selected = selected[:max_runs]

    results = []
    started = time.time()
    best = None
    for idx, params in enumerate(selected, start=1):
        row_start = time.time()
        row = evaluate(params, events)
        row["score"] = score(row)
        row["seconds"] = round(time.time() - row_start, 3)
        results.append(row)
        if best is None or row["score"] > best["score"]:
            best = row
        print(
            f"[Tuner] {idx:03d}/{len(selected)} pnl={row['net_pnl']:.2f} "
            f"final={row['final_pnl']:.2f} trades={row['trades']} wr={row['win_rate']:.2%} "
            f"hold={row.get('max_hold_seconds', 0.0):.1f}s best_wr={best['win_rate']:.2%}"
        )

    results = sorted(results, key=score, reverse=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"tick_scalping_tuning_{stamp}.csv"
    json_path = OUT_DIR / f"tick_scalping_best_{stamp}.json"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(results[:10], f, ensure_ascii=False, indent=2)

    print(f"[Tuner] Finished in {(time.time() - started):.1f}s")
    print(f"[Tuner] CSV: {csv_path}")
    print(f"[Tuner] Best JSON: {json_path}")
    print("[Tuner] Top 10:")
    for rank, row in enumerate(results[:10], start=1):
        print(
            f"#{rank} pnl={row['net_pnl']:.2f}, trades={row['trades']}, "
            f"final={row['final_pnl']:.2f}, "
            f"wr={row['win_rate']:.2%}, pf={row['profit_factor']:.2f}, "
            f"dd={row['max_trade_drawdown']:.2f}, params="
            f"shock={row['shock_window_seconds']}, tail={row['tail_prob']}, "
            f"move={row['min_move_bps']}, dr={row['directional_ratio']}, "
            f"spread={row['max_spread_ticks']}, hold={row['hold_seconds']}, "
            f"tp={row['take_profit_ticks']}, sl={row['stop_loss_ticks']}, "
            f"confirm={row['reversal_confirm_seconds']}, retrace={row['reversal_retrace_ratio']}, "
            f"order={row['order_type']}, limit={row['limit_mode']}:{row['limit_ticks']}"
        )


if __name__ == "__main__":
    max_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    main(max_runs=max_runs)
