# -*- coding: utf-8 -*-
from __future__ import annotations

import numpy as np
import pandas as pd

from config import FEE_DICT, SYMBOL_DICT, pure_product_code
from strategy.common.universe import (
    UniverseSelectionEntry,
    ensure_universe_selector,
    selection_metrics,
)
from strategy.custom.donchian_atr_breakout import DonchianATRBreakoutStrategy


TrendSelectorEntry = UniverseSelectionEntry


class TrendRankDonchianStrategy(DonchianATRBreakoutStrategy):
    """
    Donchian/ATR breakout strategy gated by a daily trend-quality selector.

    The selector uses daily bars only up to the previous trading day. It decides
    which symbols may open new positions and how the portfolio risk budget is
    split across selected symbols. Existing positions are still managed by the
    Donchian exit logic.
    """

    def __init__(
        self,
        *args,
        selector_by_date: dict | None = None,
        require_selector: bool = True,
        universe_selector=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.universe_selector = ensure_universe_selector(
            universe_selector if universe_selector is not None else (selector_by_date or {}),
            name="trend_rank",
        )
        self.require_selector = bool(require_selector)

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy TrendRankDonchian] selector_days={self.universe_selector.selected_days()} | "
            f"require_selector={self.require_selector}"
        )

    def _entry_block_reason(self, sym: str) -> str | None:
        base_reason = super()._entry_block_reason(sym)
        if base_reason:
            return base_reason
        if self.require_selector and self._selector_entry(sym) is None:
            return "not_selected_by_trend_rank"
        return None

    def _snapshot(self, sym: str, bar) -> dict:
        snapshot = super()._snapshot(sym, bar)
        entry = self._selector_entry(sym)
        snapshot["selector_date"] = self._selector_date()
        snapshot.update(selection_metrics(entry))
        return snapshot

    @staticmethod
    def _open_signal(direction: int, reason: str, metrics: dict) -> dict:
        return {
            "signal": int(direction),
            "position_mode": "target",
            "size_scale": metrics.get("indicator_selector_weight"),
            "reason": reason,
            "metrics": metrics,
        }

    def _selector_date(self) -> str:
        return self.current_time.date().isoformat()

    def _selector_entry(self, sym: str) -> TrendSelectorEntry | None:
        return self.universe_selector.entry(self._selector_date(), sym)


def build_trend_rank_selector(
    daily_wide_df: pd.DataFrame,
    backtest_start: str,
    backtest_end: str,
    top_n: int = 5,
    weight_method: str = "score",
    max_per_sector: int | None = 1,
    rebalance_days: int = 5,
    min_daily_turnover: float = 1e9,
    min_abs_ret_10d: float = 0.015,
    min_efficiency_10d: float = 0.18,
    max_cost_bps: float = 12.0,
) -> tuple[dict[str, dict[str, dict]], pd.DataFrame]:
    """
    Build a deterministic daily selector for trend-following strategies.

    The selector ranks symbols by trend strength, path efficiency, liquidity,
    and estimated trading cost. Features from day D are used for day D+1, so
    the selector does not look ahead.
    """

    long_df = _wide_daily_to_long(daily_wide_df)
    feature_df = _build_trend_features(long_df, min_daily_turnover=min_daily_turnover)
    if feature_df.empty:
        raise ValueError("Trend selector has no usable daily features")

    start_ts = pd.Timestamp(backtest_start).normalize()
    end_ts = pd.Timestamp(backtest_end).normalize()
    predict_df = feature_df[
        (feature_df["datetime"] >= start_ts - pd.Timedelta(days=30))
        & (feature_df["datetime"] <= end_ts)
    ].copy()
    if predict_df.empty:
        raise ValueError("Trend selector has no prediction rows in backtest window")

    eligible = (
        (predict_df["abs_ret_10d"] >= float(min_abs_ret_10d))
        & (predict_df["efficiency_10d"] >= float(min_efficiency_10d))
        & (predict_df["round_trip_cost_bps"] <= float(max_cost_bps))
    )
    predict_df["eligible"] = eligible

    return _build_next_day_selector_map(
        predict_df=predict_df,
        start_ts=start_ts,
        end_ts=end_ts,
        top_n=top_n,
        weight_method=weight_method,
        max_per_sector=max_per_sector,
        rebalance_days=rebalance_days,
    )


def _wide_daily_to_long(wide_df: pd.DataFrame) -> pd.DataFrame:
    if wide_df.empty:
        return pd.DataFrame()

    rows = []
    symbols = list(wide_df.columns.levels[1])
    for full_sym in symbols:
        raw_sym = _raw_code_from_wide_symbol(full_sym)
        data = pd.DataFrame(index=wide_df.index)
        for field in ["open", "high", "low", "close", "volume", "oi"]:
            key = (field, full_sym)
            if key in wide_df.columns:
                data[field] = wide_df[key]
        if "close" not in data.columns:
            continue
        data = data.reset_index().rename(columns={"index": "datetime"})
        data["symbol"] = raw_sym
        rows.append(data)

    if not rows:
        return pd.DataFrame()

    df = pd.concat(rows, ignore_index=True)
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.normalize()
    for field in ["open", "high", "low", "close", "volume", "oi"]:
        if field not in df.columns:
            df[field] = np.nan
        df[field] = pd.to_numeric(df[field], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _build_trend_features(df: pd.DataFrame, min_daily_turnover: float) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    df["multiplier"] = df["symbol"].map(lambda sym: FEE_DICT.get(pure_product_code(sym), {}).get("multiplier", 1.0))
    grouped = df.groupby("symbol", group_keys=False)

    prev_close = grouped["close"].shift(1)
    df["ret_1d"] = df["close"] / prev_close - 1.0
    df["ret_5d"] = df["close"] / grouped["close"].shift(5) - 1.0
    df["ret_10d"] = df["close"] / grouped["close"].shift(10) - 1.0
    df["ret_20d"] = df["close"] / grouped["close"].shift(20) - 1.0
    df["abs_ret_10d"] = df["ret_10d"].abs()
    df["abs_ret_20d"] = df["ret_20d"].abs()

    price_path_10 = grouped["close"].diff().abs().groupby(df["symbol"]).transform(lambda x: x.rolling(10).sum())
    price_path_20 = grouped["close"].diff().abs().groupby(df["symbol"]).transform(lambda x: x.rolling(20).sum())
    df["efficiency_10d"] = (df["close"] - grouped["close"].shift(10)).abs() / (price_path_10 + 1e-8)
    df["efficiency_20d"] = (df["close"] - grouped["close"].shift(20)).abs() / (price_path_20 + 1e-8)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_14"] = true_range.groupby(df["symbol"]).transform(lambda x: x.rolling(14).mean())
    df["atr_pct_14"] = df["atr_14"] / df["close"].replace(0, np.nan)
    df["amp_today"] = (df["high"] - df["low"]) / df["open"].replace(0, np.nan)
    df["amp_10d"] = grouped["amp_today"].transform(lambda x: x.rolling(10).mean())

    high_20 = grouped["high"].transform(lambda x: x.rolling(20).max().shift(1))
    low_20 = grouped["low"].transform(lambda x: x.rolling(20).min().shift(1))
    df["breakout_pressure"] = np.maximum(df["close"] / high_20 - 1.0, low_20 / df["close"] - 1.0)

    volume_20 = grouped["volume"].transform(lambda x: x.rolling(20).mean())
    df["volume_ratio_20d"] = df["volume"] / (volume_20 + 1e-8)
    df["turnover"] = df["volume"] * df["close"] * df["multiplier"]
    df["round_trip_cost_bps"] = [
        _round_trip_cost_bps(sym, close) for sym, close in zip(df["symbol"], df["close"])
    ]

    df = df[df["turnover"] >= min_daily_turnover].copy()
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    required = [
        "abs_ret_10d",
        "abs_ret_20d",
        "efficiency_10d",
        "efficiency_20d",
        "atr_pct_14",
        "amp_10d",
        "volume_ratio_20d",
        "turnover",
        "round_trip_cost_bps",
    ]
    df = df.dropna(subset=required).copy()
    if df.empty:
        return df

    df["rank_abs_ret_10d"] = df.groupby("datetime")["abs_ret_10d"].rank(pct=True)
    df["rank_abs_ret_20d"] = df.groupby("datetime")["abs_ret_20d"].rank(pct=True)
    df["rank_efficiency_10d"] = df.groupby("datetime")["efficiency_10d"].rank(pct=True)
    df["rank_efficiency_20d"] = df.groupby("datetime")["efficiency_20d"].rank(pct=True)
    df["rank_atr_pct"] = df.groupby("datetime")["atr_pct_14"].rank(pct=True)
    df["rank_volume_ratio"] = df.groupby("datetime")["volume_ratio_20d"].rank(pct=True)
    df["rank_turnover"] = df.groupby("datetime")["turnover"].rank(pct=True)
    df["rank_low_cost"] = df.groupby("datetime")["round_trip_cost_bps"].rank(pct=True, ascending=False)
    df["rank_breakout_pressure"] = df.groupby("datetime")["breakout_pressure"].rank(pct=True)
    df["rank_noise"] = df.groupby("datetime")["amp_10d"].rank(pct=True)

    df["selector_score"] = (
        0.24 * df["rank_abs_ret_20d"]
        + 0.20 * df["rank_abs_ret_10d"]
        + 0.18 * df["rank_efficiency_20d"]
        + 0.14 * df["rank_efficiency_10d"]
        + 0.10 * df["rank_turnover"]
        + 0.08 * df["rank_volume_ratio"]
        + 0.08 * df["rank_low_cost"]
        + 0.06 * df["rank_breakout_pressure"].fillna(0.0)
        - 0.08 * df["rank_noise"]
    )
    return df


def _build_next_day_selector_map(
    predict_df: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    top_n: int,
    weight_method: str,
    max_per_sector: int | None,
    rebalance_days: int,
) -> tuple[dict[str, dict[str, dict]], pd.DataFrame]:
    selector: dict[str, dict[str, dict]] = {}
    rows = []
    dates = sorted(pd.Timestamp(dt).normalize() for dt in predict_df["datetime"].dropna().unique())
    active_day = pd.DataFrame()
    last_rebalance_idx = -10**9

    for idx, (feature_date, trade_date) in enumerate(zip(dates, dates[1:])):
        if trade_date < start_ts or trade_date > end_ts:
            continue

        should_rebalance = active_day.empty or idx - last_rebalance_idx >= max(1, int(rebalance_days))
        if should_rebalance:
            ranked_day = predict_df[predict_df["datetime"] == feature_date].sort_values(
                "selector_score", ascending=False
            )
            eligible_day = ranked_day[ranked_day["eligible"]]
            if len(eligible_day) >= max(1, min(top_n, 3)):
                ranked_day = eligible_day
            active_day = _apply_sector_cap(ranked_day, top_n=top_n, max_per_sector=max_per_sector)
            last_rebalance_idx = idx

        if active_day.empty:
            continue

        weights = _selector_weights(active_day, weight_method)
        trade_key = trade_date.date().isoformat()
        selector[trade_key] = {}
        for rank, (row, weight) in enumerate(zip(active_day.itertuples(index=False), weights), start=1):
            sym = pure_product_code(row.symbol)
            entry = {"rank": rank, "score": float(row.selector_score), "weight": float(weight)}
            selector[trade_key][sym] = entry
            rows.append({
                "feature_date": feature_date.date().isoformat(),
                "trade_date": trade_key,
                "rank": rank,
                "symbol": sym,
                "sector": _symbol_sector(sym),
                "score": float(row.selector_score),
                "weight": float(weight),
                "abs_ret_10d": float(row.abs_ret_10d),
                "abs_ret_20d": float(row.abs_ret_20d),
                "efficiency_10d": float(row.efficiency_10d),
                "atr_pct_14": float(row.atr_pct_14),
                "turnover": float(row.turnover),
                "round_trip_cost_bps": float(row.round_trip_cost_bps),
                "eligible": bool(row.eligible),
                "rebalance_feature_date": active_day.iloc[0]["datetime"].date().isoformat()
                if "datetime" in active_day.columns
                else feature_date.date().isoformat(),
            })

    return selector, pd.DataFrame(rows)


def _selector_weights(day: pd.DataFrame, weight_method: str) -> np.ndarray:
    if weight_method == "equal":
        return np.repeat(1.0 / len(day), len(day))

    scores = day["selector_score"].astype(float).to_numpy()
    shifted = scores - np.nanmin(scores) + 1e-6
    if not np.isfinite(shifted).all() or shifted.sum() <= 0:
        return np.repeat(1.0 / len(day), len(day))
    return shifted / shifted.sum()


def _apply_sector_cap(day: pd.DataFrame, top_n: int, max_per_sector: int | None) -> pd.DataFrame:
    if not max_per_sector or max_per_sector <= 0:
        return day.head(top_n).copy()

    selected_rows = []
    sector_counts: dict[str, int] = {}
    for _, row in day.iterrows():
        sector = _symbol_sector(row["symbol"])
        if sector_counts.get(sector, 0) >= max_per_sector:
            continue
        selected_rows.append(row)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected_rows) >= top_n:
            break

    if not selected_rows:
        return day.head(0).copy()
    return pd.DataFrame(selected_rows).copy()


def _round_trip_cost_bps(symbol: str, close: float) -> float:
    meta = FEE_DICT.get(pure_product_code(symbol), {})
    multiplier = float(meta.get("multiplier", 1.0))
    notional = float(close) * multiplier
    if notional <= 0:
        return np.nan

    fee_type = str(meta.get("fee_type", "fixed")).lower()
    open_fee = float(meta.get("fee_open", 0.0))
    close_fee = float(meta.get("fee_close_history", open_fee))
    if fee_type == "ratio":
        return (open_fee + close_fee) * 10000.0
    return (open_fee + close_fee) / notional * 10000.0


def _raw_code_from_wide_symbol(full_sym) -> str:
    text = str(full_sym)
    if "(" in text and ")" in text:
        text = text.split("(", 1)[1].split(")", 1)[0]
    return pure_product_code(text)


def _symbol_sector(symbol: str) -> str:
    raw = pure_product_code(symbol)
    for code, meta in SYMBOL_DICT.items():
        if code.lower() == raw:
            return str(meta[3])
    return "unknown"
