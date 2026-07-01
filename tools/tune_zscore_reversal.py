# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import itertools
import json
import re
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analyzer.performance import StrategyAnalyzer
from backtest_engine import run_backtest
from data_feed.data_provider import DataProvider
from ui.run_from_config import STRATEGY_SPECS


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "ui" / ".runtime" / "active_report_config.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "exports" / "zscore_tuning"


GRID_MODES = {
    "quick": {
        "lookback": [8, 10, 15, 20],
        "entry_z": [1.8, 2.1, 2.4],
        "first_exit_z": [0.0],
        "final_exit_z": [0.8, 1.0],
    },
    "standard": {
        "lookback": [5, 8, 10, 12, 15, 20, 30, 45],
        "entry_z": [1.4, 1.6, 1.8, 2.0, 2.1, 2.3, 2.5, 2.8],
        "first_exit_z": [-0.5, 0.0, 0.5],
        "final_exit_z": [0.5, 1.0, 1.5],
    },
    "full": {
        "lookback": [5, 8, 10, 12, 15, 20, 30, 45, 60],
        "entry_z": [1.2, 1.4, 1.6, 1.8, 2.0, 2.1, 2.3, 2.5, 2.8, 3.0],
        "first_exit_z": [-0.75, -0.5, 0.0, 0.5, 0.75],
        "final_exit_z": [0.5, 0.8, 1.0, 1.2, 1.5, 2.0],
    },
}


def _metrics_only_generate_report(self: StrategyAnalyzer) -> None:
    self._match_trades_fifo()
    self._calculate_metrics()


def patch_report_generation() -> None:
    StrategyAnalyzer.generate_report = _metrics_only_generate_report


def patch_data_cache() -> None:
    original = DataProvider.get_history
    cache: dict[tuple[Any, ...], Any] = {}

    def cached_get_history(self, symbols, start_date, end_date, freq, data_type):
        key = (tuple(symbols), str(start_date), str(end_date), str(freq), str(data_type))
        if key not in cache:
            cache[key] = original(self, symbols, start_date, end_date, freq, data_type)
        return cache[key].copy()

    DataProvider.get_history = cached_get_history


def parse_number(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def extract_total_metrics(analyzer: StrategyAnalyzer) -> dict[str, Any]:
    metrics = analyzer.metrics_list[0] if analyzer.metrics_list else {}
    return {
        "return_pct": parse_number(metrics.get("总收益")),
        "annual_return_pct": parse_number(metrics.get("年化收益")),
        "mtm_return_pct": parse_number(metrics.get("总收益(含持仓)")),
        "mtm_annual_return_pct": parse_number(metrics.get("年化收益(含持仓)")),
        "pnl": parse_number(metrics.get("累计盈亏")),
        "max_open_value": parse_number(metrics.get("最大开仓市值")),
        "max_dd_cash": parse_number(metrics.get("单日最大回撤")),
        "max_dd_pct": parse_number(metrics.get("最大回撤率")),
        "sharpe": parse_number(metrics.get("年化Sharpe")),
        "calmar": parse_number(metrics.get("卡玛比")),
        "trade_win_rate": parse_number(metrics.get("逐笔胜率")),
        "trade_pnl_ratio": parse_number(metrics.get("逐笔盈亏比")),
        "daily_win_rate": parse_number(metrics.get("逐日胜率")),
        "daily_pnl_ratio": parse_number(metrics.get("逐日盈亏比")),
        "trade_count": int(parse_number(metrics.get("交易次数")) or 0),
        "active_trade_days": int(parse_number(metrics.get("成交日数")) or 0),
        "market_days": int(parse_number(metrics.get("行情日数")) or 0),
        "avg_daily_turnover": parse_number(metrics.get("日均成交额")),
        "commission": parse_number(metrics.get("累计手续费")),
    }


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    config["strategy"] = "zscore_reversal"
    config.setdefault("symbols", ["au"])
    config.setdefault("freq", "1d")
    config.setdefault("data_type", "main")
    config.setdefault("initial_capital", 5_000_000.0)
    return config


def build_strategy_args(config: dict[str, Any]) -> dict[str, Any]:
    spec = STRATEGY_SPECS["zscore_reversal"]
    return spec.builder(config, spec)


def build_param_grid(mode: str) -> list[dict[str, Any]]:
    grid = GRID_MODES[mode]
    keys = list(grid)
    rows = []
    for values in itertools.product(*(grid[key] for key in keys)):
        params = dict(zip(keys, values))
        if float(params["final_exit_z"]) > float(params["entry_z"]):
            continue
        rows.append(params)
    return rows


def score_row(row: dict[str, Any], min_trades: int) -> float:
    trades = int(row.get("trade_count") or 0)
    if trades < min_trades:
        return -1_000_000.0 + trades

    sharpe = float(row.get("sharpe") or 0.0)
    calmar = float(row.get("calmar") or 0.0)
    mtm_return = float(row.get("mtm_return_pct") or row.get("return_pct") or 0.0)
    max_dd = abs(float(row.get("max_dd_pct") or 0.0))
    daily_win = float(row.get("daily_win_rate") or 0.0)

    return sharpe * 100.0 + calmar * 20.0 + mtm_return * 1.5 + daily_win * 0.5 - max_dd * 2.0


def run_case(base_config: dict[str, Any], params: dict[str, Any], min_trades: int) -> dict[str, Any]:
    config = deepcopy(base_config)
    config.update(params)
    args = build_strategy_args(config)

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        analyzer = run_backtest(**args)

    row = {
        "lookback": int(params["lookback"]),
        "entry_z": float(params["entry_z"]),
        "first_exit_z": float(params["first_exit_z"]),
        "final_exit_z": float(params["final_exit_z"]),
    }
    if analyzer is None:
        row.update({"ok": False, "score": -1_000_000.0, "error": "run_backtest returned None"})
        return row

    row.update(extract_total_metrics(analyzer))
    row["ok"] = True
    row["score"] = score_row(row, min_trades=min_trades)
    return row


def write_outputs(
    rows: list[dict[str, Any]],
    base_config: dict[str, Any],
    output_dir: Path,
    started_at: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"zscore_tuning_{started_at}.csv"
    json_path = output_dir / f"zscore_tuning_{started_at}.json"
    best_config_path = output_dir / f"zscore_best_config_{started_at}.json"

    columns = [
        "score",
        "ok",
        "lookback",
        "entry_z",
        "first_exit_z",
        "final_exit_z",
        "return_pct",
        "mtm_return_pct",
        "annual_return_pct",
        "mtm_annual_return_pct",
        "sharpe",
        "calmar",
        "max_dd_pct",
        "pnl",
        "trade_count",
        "trade_win_rate",
        "trade_pnl_ratio",
        "daily_win_rate",
        "daily_pnl_ratio",
        "commission",
        "market_days",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    best = rows[0] if rows else {}
    best_config = deepcopy(base_config)
    if best:
        best_config.update({
            "lookback": int(best["lookback"]),
            "entry_z": float(best["entry_z"]),
            "first_exit_z": float(best["first_exit_z"]),
            "final_exit_z": float(best["final_exit_z"]),
        })

    summary = {
        "started_at": started_at,
        "base_config": base_config,
        "best": best,
        "top_by_score": rows[:20],
        "top_by_return": sorted(rows, key=lambda item: (item.get("mtm_return_pct") or -9999, item.get("sharpe") or -9999), reverse=True)[:20],
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    best_config_path.write_text(json.dumps(best_config, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "csv": str(csv_path),
        "json": str(json_path),
        "best_config": str(best_config_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Tune ZScoreReversalStrategy parameters.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--mode", choices=sorted(GRID_MODES), default="standard")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-trades", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of grid rows to run.")
    parser.add_argument("--sizing-value", type=float, default=None)
    parser.add_argument("--min-volume", type=int, default=None)
    parser.add_argument("--max-volume", type=int, default=None)
    args = parser.parse_args()

    patch_report_generation()
    patch_data_cache()

    base_config = load_config(args.config)
    if args.sizing_value is not None:
        base_config["sizing_value"] = float(args.sizing_value)
    if args.min_volume is not None:
        base_config["min_volume"] = int(args.min_volume)
    if args.max_volume is not None:
        base_config["max_volume"] = int(args.max_volume)
    grid = build_param_grid(args.mode)
    if args.limit > 0:
        grid = grid[: args.limit]

    started_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(
        f"[ZScoreTuner] config={args.config} mode={args.mode} cases={len(grid)} "
        f"symbols={base_config.get('symbols')} period={base_config.get('start_date')}..{base_config.get('end_date')}"
    )

    rows = []
    start = time.time()
    for index, params in enumerate(grid, start=1):
        try:
            row = run_case(base_config, params, min_trades=args.min_trades)
        except Exception as exc:
            row = {
                **params,
                "ok": False,
                "score": -1_000_000.0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        rows.append(row)
        rows.sort(key=lambda item: (item.get("score") or -1_000_000.0, item.get("mtm_return_pct") or -9999), reverse=True)
        best = rows[0]
        if index == 1 or index % 20 == 0 or index == len(grid):
            print(
                f"[ZScoreTuner] {index:>4}/{len(grid)} "
                f"best score={best.get('score', 0):>8.2f} "
                f"ret={best.get('mtm_return_pct', 0):>7.2f}% "
                f"sharpe={best.get('sharpe', 0):>5.2f} "
                f"dd={best.get('max_dd_pct', 0):>7.2f}% "
                f"trades={best.get('trade_count', 0):>3} "
                f"params=lb{best.get('lookback')} ez{best.get('entry_z')} "
                f"fx{best.get('first_exit_z')} zx{best.get('final_exit_z')}"
            )

    rows.sort(key=lambda item: (item.get("score") or -1_000_000.0, item.get("mtm_return_pct") or -9999), reverse=True)
    paths = write_outputs(rows, base_config, args.output_dir, started_at)

    elapsed = time.time() - start
    print(f"\n[ZScoreTuner] finished in {elapsed:.1f}s")
    print("[ZScoreTuner] Top 10 by risk-adjusted score:")
    for rank, row in enumerate(rows[:10], start=1):
        print(
            f"#{rank:02d} score={row.get('score', 0):>8.2f} "
            f"ret={row.get('mtm_return_pct', 0):>7.2f}% "
            f"sharpe={row.get('sharpe', 0):>5.2f} "
            f"dd={row.get('max_dd_pct', 0):>7.2f}% "
            f"trades={row.get('trade_count', 0):>3} "
            f"lb={row.get('lookback')} entry={row.get('entry_z')} "
            f"first={row.get('first_exit_z')} final={row.get('final_exit_z')}"
        )
    print(f"[ZScoreTuner] CSV: {paths['csv']}")
    print(f"[ZScoreTuner] JSON: {paths['json']}")
    print(f"[ZScoreTuner] best_config: {paths['best_config']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
