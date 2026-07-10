# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from config import pure_product_code


@dataclass
class AggregatedBar:
    """Completed OHLCV bar produced from a lower-frequency input stream."""

    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def is_fresh_bar(bar: dict | None) -> bool:
    """Return whether an aligned matrix row contains a real source bar.

    Multi-symbol aligned data repeats the last value for inactive symbols. The
    optional `is_fresh` flag prevents those stale rows from completing synthetic
    higher-timeframe bars.
    """

    if not bar:
        return False
    if "is_fresh" not in bar:
        return True
    value = bar.get("is_fresh")
    if value is None or pd.isna(value):
        return False
    return bool(value)


class IntradayBarAggregator:
    """Causal intraday OHLCV aggregator.

    The aggregator only returns the previous bucket after a later bucket starts.
    That keeps strategy logic free from current unfinished bars and avoids
    future-function leakage when running 30m/60m logic on 1m data.
    """

    _COMMODITY_DAY_SEGMENTS = ((9 * 60, 10 * 60 + 15), (10 * 60 + 30, 11 * 60 + 30), (13 * 60 + 30, 15 * 60))
    _FINANCIAL_DAY_SEGMENTS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60 + 15))
    _FINANCIAL_PRODUCTS = {"if", "ih", "ic", "im", "t", "tf", "ts", "tl"}

    def __init__(self, minutes: int):
        self.minutes = int(minutes)
        self.current_bucket: dict[str, datetime] = {}
        self.current_bar: dict[str, AggregatedBar] = {}
        self.current_count: dict[str, int] = {}

    def update(self, symbol: str, dt: datetime, raw_bar: dict) -> AggregatedBar | None:
        if not is_fresh_bar(raw_bar):
            return None
        close = raw_bar.get("close")
        if close is None or pd.isna(close):
            return None

        close = float(close)
        open_price = _float_or(raw_bar.get("open"), close)
        high = _float_or(raw_bar.get("high"), close)
        low = _float_or(raw_bar.get("low"), close)
        volume = _float_or(raw_bar.get("volume"), 0.0)
        bucket = self._bucket_start(symbol, dt)

        if symbol not in self.current_bucket:
            self.current_bucket[symbol] = bucket
            self.current_bar[symbol] = AggregatedBar(bucket, dt, open_price, high, low, close, volume)
            self.current_count[symbol] = 1
            return None

        if bucket == self.current_bucket[symbol]:
            bar = self.current_bar[symbol]
            bar.end = dt
            bar.high = max(bar.high, high)
            bar.low = min(bar.low, low)
            bar.close = close
            bar.volume += volume
            self.current_count[symbol] += 1
            return None

        completed = self.current_bar[symbol] if self.current_count[symbol] >= self.minutes else None
        self.current_bucket[symbol] = bucket
        self.current_bar[symbol] = AggregatedBar(bucket, dt, open_price, high, low, close, volume)
        self.current_count[symbol] = 1
        return completed

    def _bucket_start(self, symbol: str, dt: datetime) -> datetime:
        minute_of_day = dt.hour * 60 + dt.minute

        # Night trading is continuous across midnight. Anchor the whole night
        # session at 21:00 so 60m/120m bars do not split into short fragments.
        if minute_of_day >= 21 * 60:
            anchor = dt.replace(hour=21, minute=0, second=0, microsecond=0)
            elapsed = minute_of_day - 21 * 60
            return anchor + timedelta(minutes=(elapsed // self.minutes) * self.minutes)
        if minute_of_day < 3 * 60:
            anchor = (dt - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
            elapsed = 3 * 60 + minute_of_day
            return anchor + timedelta(minutes=(elapsed // self.minutes) * self.minutes)

        product = pure_product_code(symbol).lower()
        segments = (
            self._FINANCIAL_DAY_SEGMENTS
            if product in self._FINANCIAL_PRODUCTS
            else self._COMMODITY_DAY_SEGMENTS
        )
        session_bucket = self._day_session_bucket_start(dt, minute_of_day, segments)
        if session_bucket is not None:
            return session_bucket

        # Defensive fallback for unexpected timestamps outside known sessions.
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed_minutes = int((dt - day_start).total_seconds() // 60)
        bucket_minutes = (elapsed_minutes // self.minutes) * self.minutes
        return day_start + timedelta(minutes=bucket_minutes)

    def _day_session_bucket_start(
        self,
        dt: datetime,
        minute_of_day: int,
        segments: tuple[tuple[int, int], ...],
    ) -> datetime | None:
        elapsed_before = 0
        for start_minute, end_minute in segments:
            if start_minute <= minute_of_day < end_minute:
                elapsed = elapsed_before + minute_of_day - start_minute
                bucket_elapsed = (elapsed // self.minutes) * self.minutes
                return self._day_elapsed_to_datetime(dt, bucket_elapsed, segments)
            elapsed_before += end_minute - start_minute
        return None

    @staticmethod
    def _day_elapsed_to_datetime(
        dt: datetime,
        elapsed: int,
        segments: tuple[tuple[int, int], ...],
    ) -> datetime:
        remaining = int(elapsed)
        for start_minute, end_minute in segments:
            duration = end_minute - start_minute
            if remaining < duration:
                minute_of_day = start_minute + remaining
                return dt.replace(
                    hour=minute_of_day // 60,
                    minute=minute_of_day % 60,
                    second=0,
                    microsecond=0,
                )
            remaining -= duration

        start_minute = segments[-1][0]
        return dt.replace(
            hour=start_minute // 60,
            minute=start_minute % 60,
            second=0,
            microsecond=0,
        )


def _float_or(value, default: float) -> float:
    if value is None or pd.isna(value):
        return float(default)
    return float(value)
