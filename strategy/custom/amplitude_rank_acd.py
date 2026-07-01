# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from datetime import time, timedelta
from typing import Iterable

import lightgbm as lgb
import numpy as np
import pandas as pd

from config import FEE_DICT, SYMBOL_DICT, pure_product_code
from strategy.common.universe import (
    UniverseSelectionEntry,
    ensure_universe_selector,
    selection_metrics,
)
from strategy.custom.donchian_atr_breakout import DonchianATRBreakoutStrategy
from strategy.custom.opening_range_acd import OpeningRangeACDStrategy


FEATURE_COLUMNS = [
    "amp_today",
    "abs_ret_today",
    "vol_5d",
    "vol_10d",
    "vol_ratio",
    "is_vol_new_high",
    "volume_ratio_20d",
    "volume_change_rate",
    "oi_change_rate",
    "turnover",
    "net_capital_flow",
    "ret_1d_dir",
    "ret_5d_dir",
    "ret_10d_dir",
    "bias_ma20",
    "bb_zscore",
    "is_breakout",
    "rank_amp",
    "rank_turnover",
    "rank_ret",
    "rank_flow",
]


SelectorEntry = UniverseSelectionEntry


class AmplitudeRankACDStrategy(OpeningRangeACDStrategy):
    """
    ACD breakout strategy with a daily amplitude-rank universe selector.

    The selector only controls whether a symbol may open new positions. Existing
    positions still use the original ACD stop, trailing stop, and time exit.
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
            name="amplitude_rank",
        )
        self.require_selector = bool(require_selector)

    def on_init(self):
        super().on_init()
        selected_days = self.universe_selector.selected_days()
        print(
            f"[Strategy AmplitudeRankACD] selector_days={selected_days} | "
            f"require_selector={self.require_selector}"
        )

    def _entry_block_reason(self, sym: str) -> str | None:
        base_reason = super()._entry_block_reason(sym)
        if base_reason:
            return base_reason

        if not self.require_selector:
            return None

        if self._selector_entry(sym) is None:
            return "not_selected_by_amplitude_rank"
        return None

    def _snapshot(self, sym: str, bar, session) -> dict:
        snapshot = super()._snapshot(sym, bar, session)
        entry = self._selector_entry(sym)
        selector_date = self._selector_date()
        snapshot["selector_date"] = selector_date
        snapshot.update(selection_metrics(entry))
        return snapshot

    @staticmethod
    def _open_signal(direction: int, reason: str, metrics: dict) -> dict:
        size_scale = metrics.get("indicator_selector_weight")
        return {
            "signal": int(direction),
            "position_mode": "target",
            "size_scale": size_scale,
            "reason": reason,
            "metrics": metrics,
        }

    def _selector_date(self) -> str:
        return self._trading_date(self.current_time).isoformat()

    def _selector_entry(self, sym: str) -> SelectorEntry | None:
        return self.universe_selector.entry(self._selector_date(), sym)


class AmplitudeRankDonchianStrategy(DonchianATRBreakoutStrategy):
    """
    Donchian/ATR breakout strategy gated by the daily amplitude-rank selector.

    This pairs the high-volatility selector with a slower channel breakout,
    avoiding the very short opening-range entries used by ACD.
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
            name="amplitude_rank",
        )
        self.require_selector = bool(require_selector)

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy AmplitudeRankDonchian] selector_days={self.universe_selector.selected_days()} | "
            f"require_selector={self.require_selector}"
        )

    def _entry_block_reason(self, sym: str) -> str | None:
        base_reason = super()._entry_block_reason(sym)
        if base_reason:
            return base_reason
        if self.require_selector and self._selector_entry(sym) is None:
            return "not_selected_by_amplitude_rank"
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

    def _selector_entry(self, sym: str) -> SelectorEntry | None:
        return self.universe_selector.entry(self._selector_date(), sym)


class AmplitudeRankDayBreakoutStrategy(AmplitudeRankACDStrategy):
    """
    Daily amplitude-rank selector with intraday confirmed breakout execution.

    The LightGBM selector predicts which symbols are likely to have large
    next-day ranges. It does not predict direction. This strategy therefore
    waits for an opening-range breakout on the selected trading day and enters
    only after the breakout persists for a configurable number of bars.
    """

    def __init__(
        self,
        *args,
        confirm_bars: int = 2,
        flatten_time: str | None = "14:45",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.confirm_bars = max(1, int(confirm_bars))
        self.flatten_time = _parse_hhmm(flatten_time)
        self.breakout_confirm: dict[str, tuple[int, int]] = {sym: (0, 0) for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy AmplitudeRankDayBreakout] confirm_bars={self.confirm_bars} | "
            f"flatten_time={self.flatten_time}"
        )

    def _flat_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        blocked = self._entry_block_reason(sym)
        if blocked:
            self.breakout_confirm[sym] = (0, 0)
            return self._hold_signal(blocked, price, current_net, snapshot)
        if not snapshot["ready"]:
            self.breakout_confirm[sym] = (0, 0)
            return self._hold_signal(snapshot["reason"], price, current_net, snapshot)

        upper = snapshot["opening_high"]
        lower = snapshot["opening_low"]
        atr = snapshot["atr"]
        ema = snapshot["ema"]
        tick_size = snapshot["tick_size"]
        buffer = self.breakout_buffer_ticks * tick_size
        extension_limit = self.max_extension_atr * atr

        long_breakout = price > upper + buffer and price > ema and price - upper <= extension_limit
        short_breakout = price < lower - buffer and price < ema and lower - price <= extension_limit
        direction = 1 if long_breakout else -1 if short_breakout else 0

        previous_direction, count = self.breakout_confirm.get(sym, (0, 0))
        if direction == 0:
            self.breakout_confirm[sym] = (0, 0)
            return self._hold_signal("hold", price, current_net, snapshot)

        count = count + 1 if direction == previous_direction else 1
        self.breakout_confirm[sym] = (direction, count)
        metrics = self._metrics(price, current_net, snapshot)
        metrics["breakout_confirm_count"] = count

        if count < self.confirm_bars:
            return self._hold_signal("breakout_confirming", price, current_net, snapshot)

        self._register_entry(sym)
        reason = "confirmed_opening_range_breakout" if direction > 0 else "confirmed_opening_range_breakdown"
        return self._open_signal(direction, reason, metrics)

    def _position_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        if self._should_flatten_now():
            metrics = self._metrics(price, current_net, snapshot)
            return self._exit_signal("trading_day_end_flatten", metrics)
        return super()._position_signal(sym, price, current_net, snapshot)

    def _should_flatten_now(self) -> bool:
        if self.flatten_time is None:
            return False
        current = self.current_time.time()
        return current >= self.flatten_time and self.current_time.hour < 21


def build_amplitude_rank_selector(
    daily_wide_df: pd.DataFrame,
    backtest_start: str,
    backtest_end: str,
    top_n: int = 5,
    weight_method: str = "score",
    max_per_sector: int | None = None,
    min_daily_turnover: float = 1e9,
    max_round_trip_cost_bps: float | None = None,
    exclude_symbols: Iterable[str] | None = None,
    train_start: str | None = None,
    random_state: int = 42,
) -> tuple[dict[str, dict[str, dict]], pd.DataFrame]:
    """
    Train a fixed-sample LightGBM ranker and build daily Top-N selector maps.

    Features from day D are used to select tradable symbols for day D+1. The
    model is trained only on rows whose target date is before backtest_start.
    """

    long_df = _wide_daily_to_long(daily_wide_df)
    feature_df = _build_amplitude_features(long_df, min_daily_turnover=min_daily_turnover)
    if exclude_symbols:
        excluded = {pure_product_code(sym) for sym in exclude_symbols}
        feature_df = feature_df[~feature_df["symbol"].map(pure_product_code).isin(excluded)].copy()
    if max_round_trip_cost_bps is not None:
        feature_df = feature_df[feature_df["round_trip_cost_bps"] <= float(max_round_trip_cost_bps)].copy()

    start_ts = pd.Timestamp(backtest_start).normalize()
    end_ts = pd.Timestamp(backtest_end).normalize()
    if train_start:
        feature_df = feature_df[feature_df["datetime"] >= pd.Timestamp(train_start).normalize()].copy()

    train_df = feature_df[
        (feature_df["datetime"] < start_ts)
        & feature_df["target_amp_tomorrow"].notna()
        & feature_df["rank_label"].notna()
    ].copy()

    predict_df = feature_df[
        (feature_df["datetime"] >= start_ts - timedelta(days=10))
        & (feature_df["datetime"] <= end_ts)
    ].copy()

    if train_df.empty:
        raise ValueError("Amplitude selector has no training rows before backtest_start")
    if predict_df.empty:
        raise ValueError("Amplitude selector has no prediction rows in backtest window")

    model = _train_lgbm_ranker(train_df, random_state=random_state)
    predict_df["pred_score"] = model.predict(predict_df[FEATURE_COLUMNS])

    selector, selection_table = _build_next_day_topn_map(
        predict_df=predict_df,
        start_ts=start_ts,
        end_ts=end_ts,
        top_n=top_n,
        weight_method=weight_method,
        max_per_sector=max_per_sector,
    )
    return selector, selection_table


def _wide_daily_to_long(wide_df: pd.DataFrame) -> pd.DataFrame:
    if wide_df.empty:
        return pd.DataFrame()

    rows = []
    symbols = list(wide_df.columns.levels[1])
    for full_sym in symbols:
        match = re.search(r"\((.*?)\)", str(full_sym))
        raw_sym = pure_product_code(match.group(1) if match else str(full_sym))

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


def _build_amplitude_features(df: pd.DataFrame, min_daily_turnover: float) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    df["multiplier"] = df["symbol"].map(lambda sym: FEE_DICT.get(pure_product_code(sym), {}).get("multiplier", 1.0))

    grouped = df.groupby("symbol", group_keys=False)
    prev_close = grouped["close"].shift(1)
    prev_volume = grouped["volume"].shift(1)
    prev_oi = grouped["oi"].shift(1)

    df["amp_today"] = (df["high"] - df["low"]) / df["open"].replace(0, np.nan)
    df["abs_ret_today"] = ((df["close"] - df["open"]) / df["open"].replace(0, np.nan)).abs()
    df["log_ret"] = np.log(df["close"] / prev_close)
    df["vol_5d"] = grouped["log_ret"].transform(lambda x: x.rolling(5).std()) * np.sqrt(252)
    df["vol_10d"] = grouped["log_ret"].transform(lambda x: x.rolling(10).std()) * np.sqrt(252)
    vol_5d_mean = df.groupby("symbol")["vol_5d"].transform(lambda x: x.rolling(5).mean().shift(1))
    df["vol_ratio"] = df["vol_5d"] / (vol_5d_mean + 1e-8)
    vol_10d_max_20 = grouped["vol_10d"].transform(lambda x: x.rolling(20).max())
    df["is_vol_new_high"] = (df["vol_10d"] >= vol_10d_max_20).astype(int)

    vol_20d_mean = grouped["volume"].transform(lambda x: x.rolling(20).mean())
    df["volume_ratio_20d"] = df["volume"] / (vol_20d_mean + 1e-8)
    df["volume_change_rate"] = df["volume"] / (prev_volume + 1e-8)
    df["oi_change_rate"] = (df["oi"] - prev_oi) / (prev_oi + 1e-8)
    df["turnover"] = df["volume"] * df["close"] * df["multiplier"]
    df["net_capital_flow"] = (df["oi"] - prev_oi) * df["close"] * df["multiplier"]
    df["round_trip_cost_bps"] = [
        _round_trip_cost_bps(sym, close) for sym, close in zip(df["symbol"], df["close"])
    ]

    df["ret_1d_dir"] = df["close"] / prev_close - 1.0
    df["ret_5d_dir"] = df["close"] / grouped["close"].shift(5) - 1.0
    df["ret_10d_dir"] = df["close"] / grouped["close"].shift(10) - 1.0
    ma_20 = grouped["close"].transform(lambda x: x.rolling(20).mean())
    std_20 = grouped["close"].transform(lambda x: x.rolling(20).std())
    df["bias_ma20"] = df["close"] / ma_20 - 1.0
    df["bb_zscore"] = np.where(std_20 > 0, (df["close"] - ma_20) / std_20, 0.0)
    df["is_breakout"] = (df["bb_zscore"].abs() > 1.5).astype(int)

    df["target_amp_tomorrow"] = grouped["amp_today"].shift(-1)
    df = df[df["turnover"] >= min_daily_turnover].copy()

    df["rank_amp"] = df.groupby("datetime")["amp_today"].rank(pct=True)
    df["rank_turnover"] = df.groupby("datetime")["turnover"].rank(pct=True)
    df["rank_ret"] = df.groupby("datetime")["ret_1d_dir"].rank(pct=True)
    df["rank_flow"] = df.groupby("datetime")["net_capital_flow"].rank(pct=True)

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df = df.dropna(subset=FEATURE_COLUMNS).copy()
    labeled_groups = [_create_rank_labels(group) for _, group in df.groupby("datetime", group_keys=False)]
    if not labeled_groups:
        return pd.DataFrame(columns=list(df.columns) + ["rank_label"])
    return pd.concat(labeled_groups, ignore_index=True)


def _create_rank_labels(group: pd.DataFrame) -> pd.DataFrame:
    group = group.copy()
    if group["target_amp_tomorrow"].notna().sum() < 2:
        group["rank_label"] = np.nan
        return group

    base_score = group["target_amp_tomorrow"].rank(pct=True) * 100.0
    multiplier = np.where(group["target_amp_tomorrow"] >= 0.03, 1.0 + group["target_amp_tomorrow"] * 10.0, 1.0)
    raw_target = base_score * multiplier
    q = min(32, max(2, raw_target.notna().sum()))
    try:
        group["rank_label"] = pd.qcut(raw_target.rank(method="first"), q=q, labels=False, duplicates="drop")
    except ValueError:
        group["rank_label"] = raw_target.rank(pct=True).mul(31).fillna(0).astype(int)
    return group


def _train_lgbm_ranker(train_df: pd.DataFrame, random_state: int):
    train_df = train_df.sort_values(["datetime", "symbol"]).copy()
    train_df["rank_label"] = train_df["rank_label"].fillna(0).astype(int).clip(lower=0, upper=31)

    group_train = train_df.groupby("datetime").size().values
    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        label_gain=[(2 ** i - 1) for i in range(32)],
        num_leaves=45,
        n_estimators=300,
        learning_rate=0.03,
        random_state=random_state,
        verbosity=-1,
    )
    model.fit(train_df[FEATURE_COLUMNS], train_df["rank_label"], group=group_train)
    return model


def _build_next_day_topn_map(
    predict_df: pd.DataFrame,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    top_n: int,
    weight_method: str,
    max_per_sector: int | None,
) -> tuple[dict[str, dict[str, dict]], pd.DataFrame]:
    selector: dict[str, dict[str, dict]] = {}
    rows = []
    dates = sorted(pd.Timestamp(dt).normalize() for dt in predict_df["datetime"].dropna().unique())
    for feature_date, trade_date in zip(dates, dates[1:]):
        if trade_date < start_ts or trade_date > end_ts:
            continue

        ranked_day = predict_df[predict_df["datetime"] == feature_date].sort_values("pred_score", ascending=False)
        day = _apply_sector_cap(ranked_day, top_n=top_n, max_per_sector=max_per_sector)
        if day.empty:
            continue

        if weight_method == "equal":
            weights = np.repeat(1.0 / len(day), len(day))
        else:
            scores = day["pred_score"].astype(float).to_numpy()
            shifted = scores - np.nanmin(scores) + 1e-6
            if not np.isfinite(shifted).all() or shifted.sum() <= 0:
                weights = np.repeat(1.0 / len(day), len(day))
            else:
                weights = shifted / shifted.sum()

        trade_key = trade_date.date().isoformat()
        selector[trade_key] = {}
        for rank, (row, weight) in enumerate(zip(day.itertuples(index=False), weights), start=1):
            sym = pure_product_code(row.symbol)
            entry = {"rank": rank, "score": float(row.pred_score), "weight": float(weight)}
            selector[trade_key][sym] = entry
            rows.append({
                "feature_date": feature_date.date().isoformat(),
                "trade_date": trade_key,
                "rank": rank,
                "symbol": sym,
                "sector": _symbol_sector(sym),
                "score": float(row.pred_score),
                "weight": float(weight),
                "amp_today": float(row.amp_today),
                "turnover": float(row.turnover),
                "round_trip_cost_bps": float(row.round_trip_cost_bps),
            })

    return selector, pd.DataFrame(rows)


def _apply_sector_cap(day: pd.DataFrame, top_n: int, max_per_sector: int | None) -> pd.DataFrame:
    if not max_per_sector or max_per_sector <= 0:
        return day.head(top_n)

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
        return day.head(0)
    return pd.DataFrame(selected_rows)


def _symbol_sector(symbol: str) -> str:
    raw = pure_product_code(symbol)
    for code, meta in SYMBOL_DICT.items():
        if code.lower() == raw:
            return str(meta[3])
    return "unknown"


def _round_trip_cost_bps(symbol: str, close: float) -> float:
    raw = pure_product_code(symbol)
    meta = FEE_DICT.get(raw, {})
    multiplier = float(meta.get("multiplier", 1.0))
    notional = float(close) * multiplier
    if notional <= 0:
        return np.nan

    open_fee = float(meta.get("fee_open", 0.0))
    close_fee = max(
        float(meta.get("fee_close_history", open_fee)),
        float(meta.get("fee_close_today", open_fee)),
    )
    if str(meta.get("fee_type", "fixed")).lower() == "ratio":
        return (open_fee + close_fee) * 10000.0
    return (open_fee + close_fee) / notional * 10000.0


def _parse_hhmm(value: str | None) -> time | None:
    if value in (None, "", "none"):
        return None
    hour_text, minute_text = str(value).strip().split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))
