# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from config import CACHE_DIR, FEE_DICT, SYMBOL_DICT, build_query_symbol, pure_product_code
from data_feed.ch_loader import ClickHouseLoader


SCHEMA_VERSION = "pulse_discovery_v1"


@dataclass(frozen=True)
class PulseDiscoveryParams:
    """脉冲发现参数。

    window_seconds:
        计算极短周期价格跳变的回看窗口，例如 10 秒。
    percentile:
        触发阈值分位数，0.99 表示用当前样本内 99% 的绝对跳变作为异常线。
    min_move_ticks:
        分位数阈值之外的最低跳数过滤，避免极低波动品种产生噪声事件。
    collapse_seconds:
        相邻触发行合并窗口，同方向连续触发时只保留最强的一次。
    """

    symbols: tuple[str, ...]
    start_date: str
    end_date: str
    data_type: str = "main"
    window_seconds: float = 10.0
    percentile: float = 0.99
    min_move_ticks: float = 2.0
    collapse_seconds: float = 30.0


def run_pulse_discovery(params: PulseDiscoveryParams, force_refresh: bool = False) -> dict:
    """加载 tick 数据并生成脉冲事件、品种汇总和质量信息。"""

    artifact_dir = Path(CACHE_DIR) / "pulse_discovery"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _build_cache_key(params)
    events_path = artifact_dir / f"{cache_key}_events.parquet"
    summary_path = artifact_dir / f"{cache_key}_summary.parquet"
    meta_path = artifact_dir / f"{cache_key}_meta.json"

    raw_df = _load_tick_data(params)
    if raw_df.empty:
        return {
            "cache_key": cache_key,
            "cache_hit": False,
            "raw": raw_df,
            "events": pd.DataFrame(),
            "summary": pd.DataFrame(),
            "meta": {"status": "empty", "message": "未获取到 tick 数据。"},
        }

    if not force_refresh and events_path.exists() and summary_path.exists() and meta_path.exists():
        try:
            return {
                "cache_key": cache_key,
                "cache_hit": True,
                "raw": raw_df,
                "events": pd.read_parquet(events_path),
                "summary": pd.read_parquet(summary_path),
                "meta": json.loads(meta_path.read_text(encoding="utf-8")),
            }
        except Exception:
            # 缓存损坏时直接重算，不影响用户使用。
            pass

    events, summary, meta = discover_pulses(raw_df, params)
    if not events.empty:
        events.to_parquet(events_path, index=False)
    else:
        pd.DataFrame().to_parquet(events_path, index=False)
    summary.to_parquet(summary_path, index=False)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "cache_key": cache_key,
        "cache_hit": False,
        "raw": raw_df,
        "events": events,
        "summary": summary,
        "meta": meta,
    }


def discover_pulses(raw_df: pd.DataFrame, params: PulseDiscoveryParams) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """把 tick 长表转换成脉冲事件表和跨品种汇总表。"""

    if raw_df.empty:
        return pd.DataFrame(), pd.DataFrame(), {"status": "empty"}

    df = raw_df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)

    event_frames: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    source_rows = 0

    for full_symbol, symbol_df in df.groupby("symbol", sort=False):
        symbol_df = _prepare_tick_frame(symbol_df)
        if symbol_df.empty:
            continue

        source_rows += len(symbol_df)
        product = pure_product_code(str(full_symbol))
        tick_size = _tick_size(product)
        metrics = _window_metrics(symbol_df, params.window_seconds, tick_size)
        symbol_df = pd.concat([symbol_df.reset_index(drop=True), metrics], axis=1)

        valid_abs_moves = symbol_df["abs_move_ticks"].replace([np.inf, -np.inf], np.nan).dropna()
        if valid_abs_moves.empty:
            threshold = float(params.min_move_ticks)
        else:
            threshold = float(valid_abs_moves.quantile(params.percentile))
            threshold = max(threshold, float(params.min_move_ticks))

        candidates = symbol_df[
            (symbol_df["abs_move_ticks"] >= threshold)
            & symbol_df["move_ticks"].notna()
            & symbol_df["elapsed_seconds"].gt(0)
        ].copy()
        candidates["symbol"] = str(full_symbol)
        candidates["product"] = product
        candidates["threshold_ticks"] = threshold
        candidates["direction"] = np.where(candidates["move_ticks"] >= 0, "up", "down")
        candidates["severity"] = candidates["abs_move_ticks"] / max(threshold, 1e-12)
        candidates["severity_band"] = candidates["severity"].map(_severity_band)

        collapsed = _collapse_events(candidates, params.collapse_seconds)
        if not collapsed.empty:
            event_frames.append(collapsed)

        summary_rows.append(_summarize_symbol(
            full_symbol=str(full_symbol),
            product=product,
            df=symbol_df,
            events=collapsed,
            threshold=threshold,
            params=params,
        ))

    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["abs_move_ticks", "datetime"], ascending=[False, True]).reset_index(drop=True)

    summary = pd.DataFrame(summary_rows)
    if not summary.empty:
        summary = summary.sort_values(["pulse_count", "p99_abs_move_ticks"], ascending=[False, False]).reset_index(drop=True)

    meta = {
        "status": "ready",
        "schema_version": SCHEMA_VERSION,
        "params": asdict(params),
        "source_rows": int(source_rows),
        "event_count": int(len(events)),
        "symbol_count": int(summary["product"].nunique()) if not summary.empty else 0,
    }
    return events, summary, meta


def frame_around_event(raw_df: pd.DataFrame, event: pd.Series | dict, seconds_before: float = 60.0, seconds_after: float = 60.0) -> pd.DataFrame:
    """取某个事件前后一段 tick，用于页面事件检查器画图。"""

    if raw_df.empty or event is None:
        return pd.DataFrame()

    symbol = str(event.get("symbol", ""))
    event_time = pd.to_datetime(event.get("datetime"))
    start = event_time - pd.Timedelta(seconds=seconds_before)
    end = event_time + pd.Timedelta(seconds=seconds_after)
    view = raw_df.copy()
    view["datetime"] = pd.to_datetime(view["datetime"])
    view = view[(view["symbol"].astype(str) == symbol) & (view["datetime"] >= start) & (view["datetime"] <= end)]
    return _prepare_tick_frame(view)


def _load_tick_data(params: PulseDiscoveryParams) -> pd.DataFrame:
    query_symbols = _resolve_query_symbols(params.symbols, params.data_type)
    if not query_symbols:
        return pd.DataFrame()

    loader = ClickHouseLoader()
    return loader.get_data(
        symbols=query_symbols,
        start_date=f"{params.start_date} 00:00:00",
        end_date=f"{params.end_date} 23:59:59",
        freq="tick",
        data_type=params.data_type,
    )


def _resolve_query_symbols(symbols: Iterable[str], data_type: str) -> list[str]:
    resolved: list[str] = []
    for symbol in symbols:
        query_symbol = build_query_symbol(str(symbol), data_type)
        if query_symbol and query_symbol not in resolved:
            resolved.append(query_symbol)
    return resolved


def _build_cache_key(params: PulseDiscoveryParams) -> str:
    payload = {"schema_version": SCHEMA_VERSION, **asdict(params)}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _prepare_tick_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    result = df.copy()
    result["datetime"] = pd.to_datetime(result["datetime"])
    result = result.sort_values("datetime").reset_index(drop=True)
    numeric_cols = [
        "last_price", "volume", "bid_price_1", "bid_volume_1",
        "ask_price_1", "ask_volume_1", "oi",
    ]
    for col in numeric_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result[result["last_price"].notna() & (result["last_price"] > 0)].copy()
    if "volume_delta" not in result.columns and "volume" in result.columns:
        diff = result["volume"].diff()
        result["volume_delta"] = diff.where(diff >= 0, 0.0).fillna(0.0)
    return result.reset_index(drop=True)


def _window_metrics(df: pd.DataFrame, window_seconds: float, tick_size: float) -> pd.DataFrame:
    timestamps = df["datetime"].astype("int64").to_numpy()
    prices = df["last_price"].astype(float).to_numpy()
    window_ns = int(max(float(window_seconds), 0.001) * 1_000_000_000)
    start_idx = np.searchsorted(timestamps, timestamps - window_ns, side="left")
    start_prices = prices[start_idx]
    elapsed = (timestamps - timestamps[start_idx]) / 1_000_000_000.0

    move = prices - start_prices
    move_ticks = move / tick_size
    elapsed_safe = np.where(elapsed > 0, elapsed, np.nan)

    metrics = pd.DataFrame({
        "window_start_time": df["datetime"].iloc[start_idx].to_numpy(),
        "window_start_price": start_prices,
        "elapsed_seconds": elapsed,
        "move": move,
        "move_ticks": move_ticks,
        "abs_move_ticks": np.abs(move_ticks),
        "move_bps": np.divide(move, start_prices, out=np.zeros_like(move, dtype=float), where=start_prices != 0) * 10000.0,
        "velocity_ticks_per_sec": np.abs(move_ticks) / elapsed_safe,
    })

    if {"ask_price_1", "bid_price_1"}.issubset(df.columns):
        metrics["spread_ticks"] = (df["ask_price_1"].to_numpy(dtype=float) - df["bid_price_1"].to_numpy(dtype=float)) / tick_size
    else:
        metrics["spread_ticks"] = np.nan

    if {"bid_volume_1", "ask_volume_1"}.issubset(df.columns):
        bid = df["bid_volume_1"].to_numpy(dtype=float)
        ask = df["ask_volume_1"].to_numpy(dtype=float)
        denom = bid + ask
        metrics["book_imbalance"] = np.divide(bid - ask, denom, out=np.zeros_like(denom, dtype=float), where=denom != 0)
    else:
        metrics["book_imbalance"] = np.nan

    return metrics


def _collapse_events(candidates: pd.DataFrame, collapse_seconds: float) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()

    rows: list[pd.Series] = []
    current_group: list[int] = []
    last_time = None
    last_direction = None
    max_gap = pd.Timedelta(seconds=max(float(collapse_seconds), 0.0))

    for idx, row in candidates.sort_values("datetime").iterrows():
        row_time = pd.to_datetime(row["datetime"])
        direction = row["direction"]
        new_group = (
            not current_group
            or last_time is None
            or row_time - last_time > max_gap
            or direction != last_direction
        )
        if new_group and current_group:
            rows.append(_strongest_candidate(candidates.loc[current_group]))
            current_group = []
        current_group.append(idx)
        last_time = row_time
        last_direction = direction

    if current_group:
        rows.append(_strongest_candidate(candidates.loc[current_group]))

    return pd.DataFrame(rows).reset_index(drop=True)


def _strongest_candidate(group: pd.DataFrame) -> pd.Series:
    strongest = group.loc[group["abs_move_ticks"].idxmax()].copy()
    strongest["event_start"] = group["datetime"].min()
    strongest["event_end"] = group["datetime"].max()
    strongest["trigger_rows"] = int(len(group))
    return strongest


def _summarize_symbol(full_symbol: str, product: str, df: pd.DataFrame, events: pd.DataFrame,
                      threshold: float, params: PulseDiscoveryParams) -> dict:
    abs_moves = df["abs_move_ticks"].replace([np.inf, -np.inf], np.nan).dropna()
    duration_hours = max((df["datetime"].max() - df["datetime"].min()).total_seconds() / 3600.0, 1e-9)
    spread_coverage = float(df.get("bid_price_1", pd.Series(dtype=float)).notna().mean()) if "bid_price_1" in df else 0.0

    return {
        "symbol": full_symbol,
        "product": product,
        "rows": int(len(df)),
        "start": df["datetime"].min(),
        "end": df["datetime"].max(),
        "window_seconds": float(params.window_seconds),
        "threshold_ticks": float(threshold),
        "p95_abs_move_ticks": _quantile(abs_moves, 0.95),
        "p99_abs_move_ticks": _quantile(abs_moves, 0.99),
        "p995_abs_move_ticks": _quantile(abs_moves, 0.995),
        "pulse_count": int(len(events)),
        "pulses_per_hour": float(len(events) / duration_hours),
        "top20_avg_abs_move_ticks": float(events["abs_move_ticks"].nlargest(20).mean()) if not events.empty else 0.0,
        "avg_spread_ticks": float(df["spread_ticks"].mean()) if "spread_ticks" in df else np.nan,
        "spread_coverage": spread_coverage,
        "quality_badge": _quality_badge(len(df), spread_coverage, len(events)),
    }


def _quantile(series: pd.Series, q: float) -> float:
    if series.empty:
        return 0.0
    return float(series.quantile(q))


def _tick_size(product: str) -> float:
    meta = (
        FEE_DICT.get(product)
        or FEE_DICT.get(product.upper())
        or FEE_DICT.get(product.lower())
    )
    if meta and meta.get("tick_size"):
        return float(meta["tick_size"])

    symbol_meta = (
        SYMBOL_DICT.get(product)
        or SYMBOL_DICT.get(product.upper())
        or SYMBOL_DICT.get(product.lower())
    )
    if symbol_meta and len(symbol_meta) > 1:
        return float(symbol_meta[1])
    return 1.0


def _severity_band(severity: float) -> str:
    if severity >= 2.0:
        return "Extreme"
    if severity >= 1.5:
        return "Strong"
    if severity >= 1.0:
        return "Trigger"
    return "Watch"


def _quality_badge(rows: int, spread_coverage: float, event_count: int) -> str:
    if rows <= 0:
        return "No Data"
    if spread_coverage < 0.8:
        return "Quote Missing"
    if rows < 1000:
        return "Thin Sample"
    if event_count == 0:
        return "No Pulse"
    return "Ready"
