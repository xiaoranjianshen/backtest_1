# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Iterable

import pandas as pd

from config import FEE_DICT, pure_product_code
from data_feed.timeframe import (
    AggregatedBar as _AggBar,
    IntradayBarAggregator as _MinuteAggregator,
    is_fresh_bar as _is_fresh_bar,
)
from strategy.general_template import GeneralSignalStrategy


def _allocate_margin_weights(raw_weights: list[float], planned_count: int, total_margin_target: float) -> list[float]:
    denominator = max(float(planned_count), sum(raw_weights), 1.0)
    return [float(total_margin_target) * float(weight) / denominator for weight in raw_weights]


@dataclass(frozen=True)
class _MacdPriceBox:
    """Price range of the K-line that produced a confirmed MACD histogram peak."""

    direction: int
    high: float
    low: float
    histogram: float
    source_start: datetime
    source_end: datetime
    source_bar_index: int
    confirmed_bar_index: int


class _MacdBoxTracker:
    """Causal MACD histogram peak detector used by the trend exit rule.

    A peak is confirmed only after a later completed bar has a shorter histogram
    in the same direction, or after the histogram changes sign. The source K-line
    is therefore never selected with future data from an unfinished bar.
    """

    def __init__(self, fast_window: int = 12, slow_window: int = 26, signal_window: int = 9):
        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        self.signal_window = int(signal_window)
        self.fast_ema: float | None = None
        self.slow_ema: float | None = None
        self.signal_ema: float | None = None
        self.samples = 0
        self.wave_sign = 0
        self.wave_peak_abs = 0.0
        self.wave_peak_hist = 0.0
        self.wave_peak_bar: _AggBar | None = None
        self.wave_peak_index = -1
        self.wave_peak_confirmed = False
        self.top_box: _MacdPriceBox | None = None
        self.bottom_box: _MacdPriceBox | None = None

    def update(self, bar: _AggBar, bar_index: int) -> dict:
        close = float(bar.close)
        if self.fast_ema is None:
            self.fast_ema = close
            self.slow_ema = close
            self.signal_ema = 0.0
        else:
            self.fast_ema = _ema_step(self.fast_ema, close, self.fast_window)
            self.slow_ema = _ema_step(self.slow_ema, close, self.slow_window)

        macd = float(self.fast_ema - self.slow_ema)
        self.signal_ema = _ema_step(float(self.signal_ema), macd, self.signal_window)
        histogram = 2.0 * (macd - float(self.signal_ema))
        self.samples += 1
        ready = self.samples >= self.slow_window + self.signal_window
        confirmed_box = None

        if ready and abs(histogram) > 1e-15:
            sign = 1 if histogram > 0 else -1
            if sign != self.wave_sign:
                if self.wave_peak_bar is not None and not self.wave_peak_confirmed:
                    confirmed_box = self._confirm_peak(bar_index)
                self._start_wave(sign, histogram, bar, bar_index)
            elif abs(histogram) >= self.wave_peak_abs:
                self.wave_peak_abs = abs(histogram)
                self.wave_peak_hist = histogram
                self.wave_peak_bar = bar
                self.wave_peak_index = int(bar_index)
                self.wave_peak_confirmed = False
            elif not self.wave_peak_confirmed:
                confirmed_box = self._confirm_peak(bar_index)

        return {
            "ready": ready,
            "macd": macd,
            "macd_signal": float(self.signal_ema),
            "macd_histogram": histogram,
            "confirmed_box": confirmed_box,
            "top_box": self.top_box,
            "bottom_box": self.bottom_box,
        }

    def _start_wave(self, sign: int, histogram: float, bar: _AggBar, bar_index: int):
        self.wave_sign = int(sign)
        self.wave_peak_abs = abs(float(histogram))
        self.wave_peak_hist = float(histogram)
        self.wave_peak_bar = bar
        self.wave_peak_index = int(bar_index)
        self.wave_peak_confirmed = False

    def _confirm_peak(self, confirmed_bar_index: int) -> _MacdPriceBox | None:
        if self.wave_peak_bar is None or self.wave_sign == 0:
            return None
        box = _MacdPriceBox(
            direction=self.wave_sign,
            high=float(self.wave_peak_bar.high),
            low=float(self.wave_peak_bar.low),
            histogram=float(self.wave_peak_hist),
            source_start=self.wave_peak_bar.start,
            source_end=self.wave_peak_bar.end,
            source_bar_index=int(self.wave_peak_index),
            confirmed_bar_index=int(confirmed_bar_index),
        )
        if box.direction > 0:
            self.top_box = box
        else:
            self.bottom_box = box
        self.wave_peak_confirmed = True
        return box


@dataclass
class _SelectionEntry:
    regime: str
    percentile: float
    volatility: float
    confidence: float
    margin_pct: float
    rank: int
    avg_daily_notional: float
    margin_rate: float
    performance_score: float
    performance_trades: int
    selection_score: float


@dataclass
class _DailyTrendSelection:
    percentile: float
    volatility: float
    trend_score: float
    abs_score: float
    trend_quality: float
    margin_pct: float
    rank: int
    avg_daily_notional: float
    margin_rate: float
    selection_score: float


class VolatilityRegimeSwitchStrategy(GeneralSignalStrategy):
    """
    Volatility regime switch strategy.

    It consumes 1-minute bars, aggregates completed bars internally, and only
    emits signals after the configured reversal/trend aggregation bar has
    closed. Regime selection uses completed daily closes only.

    The regime threshold is shared by all products. Each product first computes
    its own rolling volatility percentile against its own past volatility
    history. Products above the shared threshold use the high-volatility
    reversion leg; products below 1 - threshold use the low-volatility trend
    leg; products in the middle are not tradable for that rebalance period.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        vol_window_days: int = 20,
        vol_percentile_lookback_days: int = 252,
        min_vol_percentile_samples: int = 20,
        regime_threshold: float = 0.80,
        trend_regime_threshold: float | None = None,
        reversion_minutes: int = 30,
        trend_minutes: int = 60,
        trade_start_date: str | datetime | None = None,
        selection_count: int = 5,
        rebalance_frequency: str = "weekly",
        total_margin_target: float = 0.30,
        confidence_weight: float = 0.35,
        min_selected_confidence: float = 0.05,
        min_avg_daily_notional: float = 0.0,
        min_daily_volatility: float = 0.0,
        max_symbol_notional_pct: float | None = 2.0,
        max_symbol_margin_pct: float | None = None,
        excluded_symbols: Iterable[str] | str | None = None,
        use_performance_selection: bool = False,
        performance_lookback_days: int = 22,
        performance_weight: float = 0.50,
        performance_min_trades: int = 3,
        performance_min_score: float | None = None,
        exploration_count: int = 2,
        reversion_model: str = "zscore",
        trend_model: str = "donchian",
        reversion_lookback: int = 48,
        reversion_entry_z: float = 1.8,
        reversion_exit_z: float = 0.15,
        reversion_rsi_low: float = 28.0,
        reversion_rsi_high: float = 72.0,
        reversion_atr_mult: float = 1.8,
        reversion_max_hold_bars: int = 12,
        trend_fast_window: int = 12,
        trend_slow_window: int = 48,
        trend_donchian_window: int = 36,
        trend_atr_period: int = 24,
        trend_atr_mult: float = 1.2,
        trend_exit_on_midline: bool = True,
        trend_exit_mode: str = "model",
        trend_trailing_atr_mult: float = 3.0,
        trend_macd_fast_window: int = 12,
        trend_macd_slow_window: int = 26,
        trend_macd_signal_window: int = 9,
        trend_macd_box_volume_window: int = 20,
        trend_macd_box_volume_mult: float = 0.0,
        max_entries_per_symbol_per_day: int | None = 3,
        allow_long: bool = True,
        allow_short: bool = True,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.vol_window_days = int(vol_window_days)
        self.vol_percentile_lookback_days = int(vol_percentile_lookback_days)
        self.min_vol_percentile_samples = int(min_vol_percentile_samples)
        self.regime_threshold = float(regime_threshold)
        self.trend_regime_threshold = (
            1.0 - self.regime_threshold
            if trend_regime_threshold is None
            else float(trend_regime_threshold)
        )
        self.reversion_minutes = int(reversion_minutes)
        self.trend_minutes = int(trend_minutes)
        self.trade_start_time = None if trade_start_date in (None, "") else pd.Timestamp(trade_start_date).to_pydatetime()
        self.selection_count = int(selection_count)
        self.rebalance_frequency = str(rebalance_frequency).strip().lower()
        self.total_margin_target = float(total_margin_target)
        self.confidence_weight = float(confidence_weight)
        self.min_selected_confidence = float(min_selected_confidence)
        self.min_avg_daily_notional = float(min_avg_daily_notional)
        self.min_daily_volatility = float(min_daily_volatility)
        self.max_symbol_notional_pct = (
            None if max_symbol_notional_pct in (None, 0, "") else float(max_symbol_notional_pct)
        )
        self.max_symbol_margin_pct = (
            None if max_symbol_margin_pct in (None, 0, "") else float(max_symbol_margin_pct)
        )
        self.excluded_symbols = self._normalize_symbol_set(excluded_symbols)
        self.use_performance_selection = bool(use_performance_selection)
        self.performance_lookback_days = int(performance_lookback_days)
        self.performance_weight = float(performance_weight)
        self.performance_min_trades = int(performance_min_trades)
        self.performance_min_score = (
            None if performance_min_score in (None, "") else float(performance_min_score)
        )
        self.exploration_count = max(0, int(exploration_count))

        self.reversion_model = str(reversion_model).strip().lower()
        self.trend_model = str(trend_model).strip().lower()
        self.reversion_lookback = int(reversion_lookback)
        self.reversion_entry_z = float(reversion_entry_z)
        self.reversion_exit_z = float(reversion_exit_z)
        self.reversion_rsi_low = float(reversion_rsi_low)
        self.reversion_rsi_high = float(reversion_rsi_high)
        self.reversion_atr_mult = float(reversion_atr_mult)
        self.reversion_max_hold_bars = int(reversion_max_hold_bars)
        self.trend_fast_window = int(trend_fast_window)
        self.trend_slow_window = int(trend_slow_window)
        self.trend_donchian_window = int(trend_donchian_window)
        self.trend_atr_period = int(trend_atr_period)
        self.trend_atr_mult = float(trend_atr_mult)
        self.trend_exit_on_midline = bool(trend_exit_on_midline)
        self.trend_exit_mode = str(trend_exit_mode).strip().lower()
        self.trend_trailing_atr_mult = float(trend_trailing_atr_mult)
        self.trend_macd_fast_window = int(trend_macd_fast_window)
        self.trend_macd_slow_window = int(trend_macd_slow_window)
        self.trend_macd_signal_window = int(trend_macd_signal_window)
        self.trend_macd_box_volume_window = int(trend_macd_box_volume_window)
        self.trend_macd_box_volume_mult = float(trend_macd_box_volume_mult)
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day in (None, 0, "") else int(max_entries_per_symbol_per_day)
        )
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)

        if self.vol_window_days < 5:
            raise ValueError("vol_window_days must be at least 5")
        if self.vol_percentile_lookback_days < 1:
            raise ValueError("vol_percentile_lookback_days must be positive")
        if self.min_vol_percentile_samples < 1:
            raise ValueError("min_vol_percentile_samples must be positive")
        if not 0.5 <= self.regime_threshold < 1.0:
            raise ValueError("regime_threshold must be between 0.5 and 1")
        if not 0.0 < self.trend_regime_threshold <= self.regime_threshold:
            raise ValueError("trend_regime_threshold must be > 0 and <= regime_threshold")
        if self.reversion_minutes <= 0 or self.trend_minutes <= 0:
            raise ValueError("reversion_minutes and trend_minutes must be positive")
        if self.selection_count <= 0:
            raise ValueError("selection_count must be positive")
        if self.rebalance_frequency not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance_frequency must be daily, weekly, or monthly")
        if self.total_margin_target <= 0:
            raise ValueError("total_margin_target must be positive")
        if self.trend_exit_mode not in {"model", "atr_trailing", "macd_box"}:
            raise ValueError("trend_exit_mode must be model, atr_trailing, or macd_box")
        if self.trend_trailing_atr_mult <= 0:
            raise ValueError("trend_trailing_atr_mult must be positive")
        if not 1 <= self.trend_macd_fast_window < self.trend_macd_slow_window:
            raise ValueError("trend MACD windows must satisfy 1 <= fast < slow")
        if self.trend_macd_signal_window < 1:
            raise ValueError("trend_macd_signal_window must be positive")
        if self.trend_macd_box_volume_window < 1:
            raise ValueError("trend_macd_box_volume_window must be positive")
        if self.trend_macd_box_volume_mult < 0:
            raise ValueError("trend_macd_box_volume_mult cannot be negative")

        self.contract_multipliers: dict[str, float] = {}
        self.margin_rates: dict[str, float] = {}
        for sym in self.symbols:
            meta = self.account.fee_model._get_meta_data(pure_product_code(sym))
            self.contract_multipliers[sym] = float(meta.get("multiplier", 1.0))
            self.margin_rates[sym] = float(meta.get("margin_rate", 0.1))

        rev_hist_size = max(
            self.reversion_lookback + 5,
            self.reversion_max_hold_bars + 5,
            self.performance_lookback_days * 20 + self.reversion_lookback + 10,
            120,
        )
        trend_hist_size = max(
            self.trend_slow_window + 5,
            self.trend_donchian_window + 5,
            self.trend_atr_period + 5,
            self.trend_macd_slow_window + self.trend_macd_signal_window + 5,
            self.trend_macd_box_volume_window + 5,
            self.performance_lookback_days * 10 + self.trend_slow_window + 10,
            120,
        )
        self.reversion_history: dict[str, Deque[_AggBar]] = {
            sym: deque(maxlen=rev_hist_size) for sym in self.symbols
        }
        self.trend_history: dict[str, Deque[_AggBar]] = {
            sym: deque(maxlen=trend_hist_size) for sym in self.symbols
        }
        daily_hist_size = max(
            self.vol_window_days + self.vol_percentile_lookback_days + 10,
            self.vol_window_days + self.min_vol_percentile_samples + 10,
            90,
        )
        self.daily_close_history: dict[str, Deque[float]] = {
            sym: deque(maxlen=daily_hist_size) for sym in self.symbols
        }
        self.daily_notional_history: dict[str, Deque[float]] = {
            sym: deque(maxlen=daily_hist_size) for sym in self.symbols
        }

        self.rev_aggregator = _MinuteAggregator(self.reversion_minutes)
        self.trend_aggregator = _MinuteAggregator(self.trend_minutes)
        self.latest_daily_close: dict[str, float] = {}
        self.latest_daily_notional: dict[str, float] = {}
        self.current_session_date = None
        self.active_rebalance_key = None
        self.active_selection: dict[str, _SelectionEntry] = {}
        self.force_flat_symbols: set[str] = set()
        self.selection_records: list[dict] = []

        self.reversion_bar_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.trend_bar_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.entry_bar_count: dict[str, int | None] = {sym: None for sym in self.symbols}
        self.entry_count_date: dict[str, object | None] = {sym: None for sym in self.symbols}
        self.entry_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.trend_macd_trackers = {
            sym: _MacdBoxTracker(
                fast_window=self.trend_macd_fast_window,
                slow_window=self.trend_macd_slow_window,
                signal_window=self.trend_macd_signal_window,
            )
            for sym in self.symbols
        }
        self.trend_exit_snapshots: dict[str, dict] = {}
        self.trend_trailing_high: dict[str, float | None] = {sym: None for sym in self.symbols}
        self.trend_trailing_low: dict[str, float | None] = {sym: None for sym in self.symbols}
        self.trend_trailing_position: dict[str, int] = {sym: 0 for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            "[Strategy VolRegime] "
            f"symbols={len(self.symbols)} | vol_window={self.vol_window_days} | "
            f"pct_lookback={self.vol_percentile_lookback_days} | "
            f"trend_threshold={self.trend_regime_threshold:g} | "
            f"reversion_threshold={self.regime_threshold:g} | "
            f"reversion_minutes={self.reversion_minutes} | trend_minutes={self.trend_minutes} | "
            f"selection_count={self.selection_count} | "
            f"rebalance={self.rebalance_frequency} | "
            f"reversion={self.reversion_model} | trend={self.trend_model} | "
            f"trend_exit={self.trend_exit_mode} | "
            f"margin_target={self.total_margin_target:g} | "
            f"min_notional={self.min_avg_daily_notional:g} | notional_cap={self.max_symbol_notional_pct} | "
            f"perf_select={self.use_performance_selection}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        self._update_daily_state(bar_data)
        signals: dict[str, dict] = {}

        for sym in self.symbols:
            raw_bar = bar_data.get(sym)
            if not _is_fresh_bar(raw_bar):
                continue

            close = raw_bar.get("close")
            if close is None or pd.isna(close):
                continue

            completed_reversion = self.rev_aggregator.update(sym, self.current_time, raw_bar)
            completed_trend = self.trend_aggregator.update(sym, self.current_time, raw_bar)
            if completed_reversion is not None:
                self.reversion_bar_count[sym] += 1
            if completed_trend is not None:
                self.trend_bar_count[sym] += 1
                self.trend_exit_snapshots[sym] = self.trend_macd_trackers[sym].update(
                    completed_trend,
                    self.trend_bar_count[sym],
                )

            if self.trade_start_time is not None and self.current_time < self.trade_start_time:
                self._append_completed_bars(sym, completed_reversion, completed_trend)
                continue

            selection = self.active_selection.get(sym)
            current_net = self.get_net_position(sym)

            if sym in self.force_flat_symbols:
                if current_net == 0:
                    self.force_flat_symbols.discard(sym)
                else:
                    signals[sym] = self._flat_signal(sym, "regime_or_universe_change", float(close), current_net)
                    self._append_completed_bars(sym, completed_reversion, completed_trend)
                    continue

            if selection is None:
                if current_net != 0:
                    signals[sym] = self._flat_signal(sym, "not_selected_this_period", float(close), current_net)
                self._append_completed_bars(sym, completed_reversion, completed_trend)
                continue

            if selection.regime == "reversion":
                if completed_reversion is not None:
                    signal = self._reversion_signal(sym, completed_reversion, selection)
                    if signal is not None:
                        signals[sym] = signal
            else:
                if completed_trend is not None:
                    signal = self._trend_signal(sym, completed_trend, selection)
                    if signal is not None:
                        signals[sym] = signal
            self._append_completed_bars(sym, completed_reversion, completed_trend)

        return signals

    def _append_completed_bars(self, sym: str, completed_reversion: _AggBar | None, completed_trend: _AggBar | None):
        if completed_reversion is not None:
            self.reversion_history[sym].append(completed_reversion)
        if completed_trend is not None:
            self.trend_history[sym].append(completed_trend)

    def _update_daily_state(self, bar_data: dict):
        session_date = self.current_time.date()
        if self.current_session_date is None:
            self.current_session_date = session_date

        if session_date != self.current_session_date and self._can_roll_session_now():
            self._commit_daily_close()
            self.current_session_date = session_date
            self.latest_daily_close = {}
            self.latest_daily_notional = {}
            self._maybe_reselect_universe(session_date)

        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not _is_fresh_bar(bar):
                continue
            close = bar.get("close")
            if close is not None and not pd.isna(close):
                close = float(close)
                self.latest_daily_close[sym] = close
                volume = _float_or(bar.get("volume"), 0.0)
                if volume > 0:
                    self.latest_daily_notional[sym] = (
                        self.latest_daily_notional.get(sym, 0.0)
                        + volume * close * self.contract_multipliers.get(sym, 1.0)
                    )

    def _can_roll_session_now(self) -> bool:
        # Avoid rebalance/selection during the night session after midnight.
        # The first version of this research framework treats the daytime open
        # as the earliest safe point to commit the previous session and refresh
        # the tradable universe.
        return self.current_time.hour >= 9

    def _commit_daily_close(self):
        daily_hist_size = max(
            self.vol_window_days + self.vol_percentile_lookback_days + 10,
            self.vol_window_days + self.min_vol_percentile_samples + 10,
            90,
        )
        for sym, close in self.latest_daily_close.items():
            if close > 0:
                self.daily_close_history.setdefault(sym, deque(maxlen=daily_hist_size)).append(close)
                self.daily_notional_history.setdefault(sym, deque(maxlen=daily_hist_size)).append(
                    float(self.latest_daily_notional.get(sym, 0.0))
                )

    def _maybe_reselect_universe(self, trade_date):
        rebalance_key = self._rebalance_key(trade_date)
        if self.active_rebalance_key == rebalance_key:
            return

        selection = self._build_selection()
        if selection is None:
            return

        old_selection = self.active_selection
        self.active_selection = selection
        self.active_rebalance_key = rebalance_key

        changed = set(old_selection) - set(selection)
        for sym, old_entry in old_selection.items():
            new_entry = selection.get(sym)
            if new_entry is not None and new_entry.regime != old_entry.regime:
                changed.add(sym)
        self.force_flat_symbols.update(changed)

        high_count = sum(1 for item in selection.values() if item.regime == "reversion")
        low_count = len(selection) - high_count
        self._record_selection_snapshot(trade_date, rebalance_key, selection, high_count, low_count)
        print(
            f"[Strategy VolRegime] {trade_date} universe selected: "
            f"{len(selection)} symbols | reversion={high_count} | trend={low_count}"
        )

    def _record_selection_snapshot(self, trade_date, rebalance_key, selection, high_count: int, low_count: int):
        self.selection_records.append({
            "datetime": self.current_time,
            "trade_date": trade_date,
            "rebalance_key": str(rebalance_key),
            "record_type": "summary",
            "selected_count": len(selection),
            "reversion_count": high_count,
            "trend_count": low_count,
        })
        for sym, entry in sorted(selection.items(), key=lambda item: item[1].rank):
            self.selection_records.append({
                "datetime": self.current_time,
                "trade_date": trade_date,
                "rebalance_key": str(rebalance_key),
                "record_type": "symbol",
                "symbol": sym,
                "regime": entry.regime,
                "vol_percentile": entry.percentile,
                "volatility": entry.volatility,
                "regime_confidence": entry.confidence,
                "target_margin_pct": entry.margin_pct,
                "rank": entry.rank,
                "avg_daily_notional": entry.avg_daily_notional,
                "margin_rate": entry.margin_rate,
                "performance_score": entry.performance_score,
                "performance_trades": entry.performance_trades,
                "selection_score": entry.selection_score,
            })

    def _rebalance_key(self, trade_date):
        if self.rebalance_frequency == "daily":
            return ("daily", trade_date)
        if self.rebalance_frequency == "weekly":
            iso_year, iso_week, _ = trade_date.isocalendar()
            return ("weekly", iso_year, iso_week)
        return ("monthly", trade_date.year, trade_date.month)

    def _build_selection(self) -> dict[str, _SelectionEntry] | None:
        candidates = []
        eligible_count = 0
        low_threshold = self.trend_regime_threshold
        for sym, history in self.daily_close_history.items():
            if sym in self.excluded_symbols:
                continue
            closes = list(history)
            vol_series = _rolling_volatility_series(closes, self.vol_window_days)
            if len(vol_series) < self.min_vol_percentile_samples + 1:
                continue
            current_vol = vol_series[-1]
            history_vols = vol_series[:-1]
            if self.vol_percentile_lookback_days > 0:
                history_vols = history_vols[-self.vol_percentile_lookback_days:]
            if len(history_vols) < self.min_vol_percentile_samples:
                continue
            eligible_count += 1
            vol = current_vol
            if vol <= 0 or not math.isfinite(vol):
                continue
            if self.min_daily_volatility > 0 and vol < self.min_daily_volatility:
                continue
            avg_notional = self._average_daily_notional(sym)
            if self.min_avg_daily_notional > 0 and avg_notional < self.min_avg_daily_notional:
                continue

            pct = _percentile_rank(current_vol, history_vols)
            if pct >= self.regime_threshold:
                regime = "reversion"
                confidence = (pct - self.regime_threshold) / max(1.0 - self.regime_threshold, 1e-9)
            elif pct <= low_threshold:
                regime = "trend"
                confidence = (low_threshold - pct) / max(low_threshold, 1e-9)
            else:
                continue
            confidence = max(0.0, min(1.0, confidence))
            if confidence < self.min_selected_confidence:
                continue
            performance = self._rolling_performance_score(sym, regime)
            if self._is_performance_rejected(performance):
                continue
            candidates.append({
                "distance": confidence,
                "confidence": confidence,
                "sym": sym,
                "vol": vol,
                "pct": pct,
                "regime": regime,
                "avg_notional": avg_notional,
                "performance": performance,
            })

        if eligible_count == 0:
            return None

        selected = self._select_candidates(candidates)
        if not selected:
            return {}

        raw_weights = [
            max(0.05, 1.0 + self.confidence_weight * (row["confidence"] - 0.5))
            * max(0.20, 1.0 + self.performance_weight * max(-0.5, min(0.5, row["performance"]["score"])))
            for row in selected
        ]
        # Keep one risk slot per planned selection. If fewer symbols qualify,
        # unused slots remain cash instead of concentrating the full portfolio
        # margin target into a small number of contracts.
        margin_weights = _allocate_margin_weights(
            raw_weights,
            planned_count=self.selection_count,
            total_margin_target=self.total_margin_target,
        )

        selection: dict[str, _SelectionEntry] = {}
        for rank, (row, margin_pct) in enumerate(zip(selected, margin_weights), start=1):
            confidence = row["confidence"]
            sym = row["sym"]
            vol = row["vol"]
            pct = row["pct"]
            regime = row["regime"]
            avg_notional = row["avg_notional"]
            performance = row["performance"]
            margin_rate = self.margin_rates.get(sym, 0.1)
            margin_pct = self._apply_margin_caps(margin_pct, margin_rate)
            selection[sym] = _SelectionEntry(
                regime=regime,
                percentile=float(pct),
                volatility=float(vol),
                confidence=float(confidence),
                margin_pct=margin_pct,
                rank=rank,
                avg_daily_notional=float(avg_notional),
                margin_rate=float(margin_rate),
                performance_score=float(performance["score"]),
                performance_trades=int(performance["trades"]),
                selection_score=float(row.get("selection_score", row["distance"])),
            )
        return selection

    def _select_candidates(self, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return []
        if not self.use_performance_selection:
            return sorted(candidates, key=lambda row: row["distance"], reverse=True)[: self.selection_count]

        scored = []
        explore = []
        for row in candidates:
            perf = row["performance"]
            if perf["trades"] >= self.performance_min_trades:
                scored.append(row)
            else:
                explore.append(row)

        scored.sort(key=lambda row: row["performance"]["score"], reverse=True)
        total = len(scored)
        for idx, row in enumerate(scored):
            perf_rank = 1.0 if total <= 1 else 1.0 - idx / (total - 1)
            row["selection_score"] = (
                (1.0 - self.performance_weight) * row["confidence"]
                + self.performance_weight * perf_rank
            )

        core_count = max(0, self.selection_count - min(self.exploration_count, self.selection_count))
        selected = sorted(scored, key=lambda row: row["selection_score"], reverse=True)[:core_count]
        used = {row["sym"] for row in selected}
        exploration_pool = [row for row in candidates if row["sym"] not in used]
        exploration_pool.sort(key=lambda row: row["confidence"], reverse=True)
        selected.extend(exploration_pool[: max(0, self.selection_count - len(selected))])
        return selected[: self.selection_count]

    def _is_performance_rejected(self, performance: dict) -> bool:
        if not self.use_performance_selection:
            return False
        if self.performance_min_score is None:
            return False
        if performance["trades"] < self.performance_min_trades:
            return False
        return float(performance["score"]) < self.performance_min_score

    def _rolling_performance_score(self, sym: str, regime: str) -> dict:
        if not self.use_performance_selection:
            return {"score": 0.0, "trades": 0, "total_return": 0.0, "win_rate": 0.0, "max_drawdown": 0.0}

        history = list(self.reversion_history[sym] if regime == "reversion" else self.trend_history[sym])
        if not history:
            return {"score": 0.0, "trades": 0, "total_return": 0.0, "win_rate": 0.0, "max_drawdown": 0.0}
        cutoff = self.current_time - timedelta(days=max(1, self.performance_lookback_days))
        if regime == "reversion":
            trades = self._simulate_reversion_performance(history, cutoff)
        else:
            trades = self._simulate_trend_performance(history, cutoff)
        if not trades:
            return {"score": 0.0, "trades": 0, "total_return": 0.0, "win_rate": 0.0, "max_drawdown": 0.0}
        total_return = sum(trades)
        win_rate = sum(1 for item in trades if item > 0) / len(trades)
        max_drawdown = _max_drawdown_from_returns(trades)
        avg_return = total_return / len(trades)
        score = total_return + 0.25 * (win_rate - 0.5) + 0.5 * avg_return - 0.50 * abs(max_drawdown)
        return {
            "score": float(score),
            "trades": len(trades),
            "total_return": float(total_return),
            "win_rate": float(win_rate),
            "max_drawdown": float(max_drawdown),
        }

    def _simulate_reversion_performance(self, history: list[_AggBar], cutoff: datetime) -> list[float]:
        lookback = self.reversion_lookback
        trades: list[float] = []
        position = 0
        entry_price = 0.0
        entry_time = None
        entry_index = 0
        for idx, bar in enumerate(history):
            prior = history[:idx]
            if len(prior) < lookback:
                continue
            direction = 0
            exit_now = False
            model = self.reversion_model
            if model in {"zscore", "bollinger"}:
                closes = [item.close for item in prior[-lookback:]]
                mean = sum(closes) / len(closes)
                sigma = _std(closes)
                if sigma <= 0:
                    continue
                zscore = (bar.close - mean) / sigma
                if position == 0:
                    if zscore >= self.reversion_entry_z:
                        direction = -1
                    elif zscore <= -self.reversion_entry_z:
                        direction = 1
                elif position > 0 and zscore >= self.reversion_exit_z:
                    exit_now = True
                elif position < 0 and zscore <= -self.reversion_exit_z:
                    exit_now = True
            elif model == "rsi":
                closes = [item.close for item in prior[-lookback:]] + [bar.close]
                rsi = _rsi(closes)
                if position == 0:
                    if rsi >= self.reversion_rsi_high:
                        direction = -1
                    elif rsi <= self.reversion_rsi_low:
                        direction = 1
                elif position > 0 and rsi >= 50.0:
                    exit_now = True
                elif position < 0 and rsi <= 50.0:
                    exit_now = True
            elif model == "donchian_fade":
                sample = prior[-lookback:]
                upper = max(item.high for item in sample)
                lower = min(item.low for item in sample)
                mid = (upper + lower) / 2.0
                buffer = 2.0 * self._tick_size_from_history_symbol(history, bar)
                if position == 0:
                    if bar.close >= upper - buffer:
                        direction = -1
                    elif bar.close <= lower + buffer:
                        direction = 1
                elif position > 0 and bar.close >= mid:
                    exit_now = True
                elif position < 0 and bar.close <= mid:
                    exit_now = True
            elif model == "atr_fade":
                need = max(lookback, self.trend_atr_period + 1)
                if len(prior) < need:
                    continue
                atr = _atr(prior[-(self.trend_atr_period + 1):])
                start_price = prior[-min(6, len(prior))].close
                impulse = bar.close - start_price
                if position == 0 and atr > 0:
                    if impulse >= self.reversion_atr_mult * atr:
                        direction = -1
                    elif impulse <= -self.reversion_atr_mult * atr:
                        direction = 1

            if (
                position != 0
                and self.reversion_max_hold_bars > 0
                and idx - entry_index >= self.reversion_max_hold_bars
            ):
                exit_now = True

            if position == 0 and direction != 0:
                position = direction
                entry_price = bar.close
                entry_time = bar.end
                entry_index = idx
            elif position != 0 and exit_now:
                if entry_time is not None and entry_time >= cutoff:
                    trades.append(position * (bar.close / entry_price - 1.0))
                position = 0
        if position != 0 and entry_time is not None and entry_time >= cutoff:
            trades.append(position * (history[-1].close / entry_price - 1.0))
        return trades

    def _simulate_trend_performance(self, history: list[_AggBar], cutoff: datetime) -> list[float]:
        trades: list[float] = []
        position = 0
        entry_price = 0.0
        entry_time = None
        entry_index = -1
        model = self.trend_model
        tracker = _MacdBoxTracker(
            fast_window=self.trend_macd_fast_window,
            slow_window=self.trend_macd_slow_window,
            signal_window=self.trend_macd_signal_window,
        )
        trailing_high = None
        trailing_low = None
        for idx, bar in enumerate(history):
            macd_snapshot = tracker.update(bar, idx)
            prior = history[:idx]
            direction = 0
            exit_now = False
            if model in {"ma_cross", "ema_cross"}:
                closes = [item.close for item in prior] + [bar.close]
                if len(closes) < self.trend_slow_window:
                    continue
                if model == "ema_cross":
                    fast = _ema(closes[-self.trend_slow_window:], self.trend_fast_window)
                    slow = _ema(closes[-self.trend_slow_window:], self.trend_slow_window)
                else:
                    fast = sum(closes[-self.trend_fast_window:]) / self.trend_fast_window
                    slow = sum(closes[-self.trend_slow_window:]) / self.trend_slow_window
                direction = 1 if fast > slow else -1 if fast < slow else 0
                exit_now = bool(
                    self.trend_exit_mode == "model"
                    and position
                    and direction
                    and position != direction
                )
            elif model == "donchian":
                if len(prior) < self.trend_donchian_window:
                    continue
                sample = prior[-self.trend_donchian_window:]
                upper = max(item.high for item in sample)
                lower = min(item.low for item in sample)
                mid = (upper + lower) / 2.0
                buffer = 0.0
                if position == 0:
                    if bar.close > upper + buffer:
                        direction = 1
                    elif bar.close < lower - buffer:
                        direction = -1
                elif position > 0 and self.trend_exit_mode == "model" and self.trend_exit_on_midline and bar.close < mid:
                    exit_now = True
                elif position < 0 and self.trend_exit_mode == "model" and self.trend_exit_on_midline and bar.close > mid:
                    exit_now = True
            elif model == "atr_breakout":
                need = max(self.trend_atr_period + 1, self.trend_slow_window)
                if len(prior) < need:
                    continue
                closes = [item.close for item in prior[-self.trend_slow_window:]]
                mean = sum(closes) / len(closes)
                atr = _atr(prior[-(self.trend_atr_period + 1):])
                if atr <= 0:
                    continue
                upper = mean + self.trend_atr_mult * atr
                lower = mean - self.trend_atr_mult * atr
                if position == 0:
                    if bar.close > upper:
                        direction = 1
                    elif bar.close < lower:
                        direction = -1
                elif position > 0 and self.trend_exit_mode == "model" and bar.close < mean:
                    exit_now = True
                elif position < 0 and self.trend_exit_mode == "model" and bar.close > mean:
                    exit_now = True

            if position != 0 and self.trend_exit_mode == "atr_trailing":
                atr_sample = history[max(0, idx - self.trend_atr_period):idx + 1]
                atr = _atr(atr_sample)
                if atr > 0:
                    if position > 0:
                        trailing_high = max(float(bar.high), float(trailing_high)) if trailing_high is not None else float(bar.high)
                        exit_now = bar.close < trailing_high - self.trend_trailing_atr_mult * atr
                    else:
                        trailing_low = min(float(bar.low), float(trailing_low)) if trailing_low is not None else float(bar.low)
                        exit_now = bar.close > trailing_low + self.trend_trailing_atr_mult * atr

            if position != 0 and self.trend_exit_mode == "macd_box":
                box = macd_snapshot.get("top_box") if position > 0 else macd_snapshot.get("bottom_box")
                if box is not None and box.confirmed_bar_index > entry_index:
                    volume_confirmed = True
                    if self.trend_macd_box_volume_mult > 0:
                        volumes = [
                            float(item.volume)
                            for item in prior[-self.trend_macd_box_volume_window:]
                            if item.volume > 0
                        ]
                        average_volume = sum(volumes) / len(volumes) if volumes else 0.0
                        directional_candle = bar.close < bar.open if position > 0 else bar.close > bar.open
                        volume_confirmed = bool(
                            average_volume > 0
                            and directional_candle
                            and bar.volume >= average_volume * self.trend_macd_box_volume_mult
                        )
                    if volume_confirmed:
                        exit_now = bar.close < box.low if position > 0 else bar.close > box.high

            exited_this_bar = False
            if position != 0 and exit_now:
                if entry_time is not None and entry_time >= cutoff:
                    trades.append(position * (bar.close / entry_price - 1.0))
                position = 0
                trailing_high = None
                trailing_low = None
                exited_this_bar = True
            if position == 0 and direction != 0 and not exited_this_bar:
                position = direction
                entry_price = bar.close
                entry_time = bar.end
                entry_index = idx
                trailing_high = float(bar.high) if position > 0 else None
                trailing_low = float(bar.low) if position < 0 else None
        if position != 0 and entry_time is not None and entry_time >= cutoff:
            trades.append(position * (history[-1].close / entry_price - 1.0))
        return trades

    def _tick_size_from_history_symbol(self, history: list[_AggBar], bar: _AggBar) -> float:
        # Virtual scoring only needs a small breakout buffer; exact symbol tick is
        # handled in live signal generation where the symbol is available.
        return max(abs(bar.close) * 0.0001, 1e-9)

    def _average_daily_notional(self, sym: str) -> float:
        history = list(self.daily_notional_history.get(sym, []))
        if not history:
            return 0.0
        sample = history[-self.vol_window_days:]
        valid = [item for item in sample if item > 0]
        if not valid:
            return 0.0
        return float(sum(valid) / len(valid))

    def _apply_margin_caps(self, margin_pct: float, margin_rate: float) -> float:
        capped = float(margin_pct)
        if self.max_symbol_margin_pct is not None:
            capped = min(capped, self.max_symbol_margin_pct)
        if self.max_symbol_notional_pct is not None:
            capped = min(capped, float(self.max_symbol_notional_pct) * float(margin_rate))
        return max(0.0, capped)

    def _reversion_signal(self, sym: str, bar: _AggBar, selection: _SelectionEntry) -> dict | None:
        current_net = self.get_net_position(sym)
        model = self.reversion_model

        signal = None
        if model in {"zscore", "bollinger"}:
            signal = self._reversion_zscore(sym, bar, selection, current_net)
        elif model == "rsi":
            signal = self._reversion_rsi(sym, bar, selection, current_net)
        elif model == "donchian_fade":
            signal = self._reversion_donchian_fade(sym, bar, selection, current_net)
        elif model == "atr_fade":
            signal = self._reversion_atr_fade(sym, bar, selection, current_net)
        else:
            raise ValueError(f"Unsupported reversion_model: {self.reversion_model}")

        if current_net != 0 and self.reversion_max_hold_bars > 0:
            entry_bar = self.entry_bar_count.get(sym)
            if entry_bar is not None:
                held = self.reversion_bar_count[sym] - entry_bar
                already_exiting = bool(signal and signal.get("position_mode") == "flat")
                if held >= self.reversion_max_hold_bars and not already_exiting:
                    metrics = dict((signal or {}).get("metrics") or {})
                    if not metrics:
                        metrics = self._base_metrics(sym, bar, selection, current_net, {})
                    metrics["holding_reversion_bars"] = held
                    return self._exit_signal(sym, "reversion_time_exit", metrics)

        return signal

    def _trend_signal(self, sym: str, bar: _AggBar, selection: _SelectionEntry) -> dict | None:
        current_net = self.get_net_position(sym)
        model = self.trend_model

        signal = None
        if model in {"ma_cross", "ema_cross"}:
            signal = self._trend_ma_cross(sym, bar, selection, current_net, use_ema=(model == "ema_cross"))
        elif model == "donchian":
            signal = self._trend_donchian(sym, bar, selection, current_net)
        elif model == "atr_breakout":
            signal = self._trend_atr_breakout(sym, bar, selection, current_net)
        else:
            raise ValueError(f"Unsupported trend_model: {self.trend_model}")

        if current_net == 0:
            self._reset_trend_trailing_state(sym)
            return signal
        if self.trend_exit_mode == "model":
            return signal

        custom_exit = self._trend_custom_exit(sym, bar, selection, current_net, signal)
        if custom_exit is not None:
            return custom_exit
        return signal

    def _trend_custom_exit(
        self,
        sym: str,
        bar: _AggBar,
        selection: _SelectionEntry,
        current_net: int,
        base_signal: dict | None,
    ) -> dict | None:
        metrics = dict((base_signal or {}).get("metrics") or {})
        if not metrics:
            metrics = self._base_metrics(sym, bar, selection, current_net, {})
        metrics["trend_exit_mode"] = self.trend_exit_mode

        if self.trend_exit_mode == "atr_trailing":
            return self._trend_atr_trailing_exit(sym, bar, current_net, metrics)
        if self.trend_exit_mode == "macd_box":
            return self._trend_macd_box_exit(sym, bar, current_net, metrics)
        return None

    def _trend_atr_trailing_exit(self, sym: str, bar: _AggBar, current_net: int, metrics: dict) -> dict | None:
        history = list(self.trend_history[sym]) + [bar]
        need = self.trend_atr_period + 1
        if len(history) < need:
            return None
        atr = _atr(history[-need:])
        if atr <= 0:
            return None

        previous_position = self.trend_trailing_position.get(sym, 0)
        if current_net > 0:
            prior_high = self.trend_trailing_high.get(sym) if previous_position > 0 else None
            trailing_high = max(float(bar.high), float(prior_high)) if prior_high is not None else float(bar.high)
            trailing_stop = trailing_high - self.trend_trailing_atr_mult * atr
            self.trend_trailing_high[sym] = trailing_high
            self.trend_trailing_low[sym] = None
            self.trend_trailing_position[sym] = 1
            metrics.update({"atr": atr, "trailing_extreme": trailing_high, "trailing_stop": trailing_stop})
            if bar.close < trailing_stop:
                return self._exit_signal(sym, "trend_atr_trailing_exit", metrics)
        else:
            prior_low = self.trend_trailing_low.get(sym) if previous_position < 0 else None
            trailing_low = min(float(bar.low), float(prior_low)) if prior_low is not None else float(bar.low)
            trailing_stop = trailing_low + self.trend_trailing_atr_mult * atr
            self.trend_trailing_low[sym] = trailing_low
            self.trend_trailing_high[sym] = None
            self.trend_trailing_position[sym] = -1
            metrics.update({"atr": atr, "trailing_extreme": trailing_low, "trailing_stop": trailing_stop})
            if bar.close > trailing_stop:
                return self._exit_signal(sym, "trend_atr_trailing_exit", metrics)
        return None

    def _trend_macd_box_exit(self, sym: str, bar: _AggBar, current_net: int, metrics: dict) -> dict | None:
        snapshot = self.trend_exit_snapshots.get(sym) or {}
        box = snapshot.get("top_box") if current_net > 0 else snapshot.get("bottom_box")
        metrics.update({
            "macd": snapshot.get("macd"),
            "macd_signal": snapshot.get("macd_signal"),
            "macd_histogram": snapshot.get("macd_histogram"),
            "macd_box_ready": bool(snapshot.get("ready")),
        })
        if box is None:
            return None

        entry_bar = self.entry_bar_count.get(sym)
        box_is_post_entry = entry_bar is None or box.confirmed_bar_index > entry_bar
        volume_confirmed, average_volume = self._macd_box_volume_confirmed(sym, bar, current_net)
        metrics.update({
            "macd_box_high": box.high,
            "macd_box_low": box.low,
            "macd_box_histogram": box.histogram,
            "macd_box_source_start": box.source_start,
            "macd_box_source_end": box.source_end,
            "macd_box_confirmed_bar": box.confirmed_bar_index,
            "macd_box_post_entry": box_is_post_entry,
            "macd_box_average_volume": average_volume,
            "macd_box_volume_confirmed": volume_confirmed,
        })
        if not box_is_post_entry or not volume_confirmed:
            return None
        if current_net > 0 and bar.close < box.low:
            return self._exit_signal(sym, "trend_macd_top_box_break_exit", metrics)
        if current_net < 0 and bar.close > box.high:
            return self._exit_signal(sym, "trend_macd_bottom_box_break_exit", metrics)
        return None

    def _macd_box_volume_confirmed(self, sym: str, bar: _AggBar, current_net: int) -> tuple[bool, float | None]:
        if self.trend_macd_box_volume_mult <= 0:
            return True, None
        history = list(self.trend_history[sym])[-self.trend_macd_box_volume_window:]
        volumes = [float(item.volume) for item in history if item.volume > 0]
        if not volumes:
            return False, None
        average_volume = sum(volumes) / len(volumes)
        directional_candle = bar.close < bar.open if current_net > 0 else bar.close > bar.open
        volume_ok = bar.volume >= average_volume * self.trend_macd_box_volume_mult
        return bool(directional_candle and volume_ok), average_volume

    def _reset_trend_trailing_state(self, sym: str):
        self.trend_trailing_high[sym] = None
        self.trend_trailing_low[sym] = None
        self.trend_trailing_position[sym] = 0

    def _reversion_zscore(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        closes = [item.close for item in self.reversion_history[sym]]
        if len(closes) < self.reversion_lookback:
            return self._hold_signal(sym, "reversion_warming_up", bar, selection, current_net)
        sample = closes[-self.reversion_lookback:]
        mean = sum(sample) / len(sample)
        sigma = _std(sample)
        if sigma <= 0:
            return self._hold_signal(sym, "zero_reversion_sigma", bar, selection, current_net)
        zscore = (bar.close - mean) / sigma
        metrics = self._base_metrics(sym, bar, selection, current_net, {"mean": mean, "sigma": sigma, "zscore": zscore})

        if current_net == 0:
            if zscore >= self.reversion_entry_z:
                return self._entry_signal(sym, -1, "high_vol_zscore_short", selection, metrics)
            if zscore <= -self.reversion_entry_z:
                return self._entry_signal(sym, 1, "high_vol_zscore_long", selection, metrics)
        elif current_net > 0 and zscore >= self.reversion_exit_z:
            return self._exit_signal(sym, "zscore_mean_revert_exit", metrics)
        elif current_net < 0 and zscore <= -self.reversion_exit_z:
            return self._exit_signal(sym, "zscore_mean_revert_exit", metrics)
        return {"signal": None, "reason": "reversion_hold", "metrics": metrics}

    def _reversion_rsi(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        closes = [item.close for item in self.reversion_history[sym]] + [bar.close]
        if len(closes) < self.reversion_lookback + 1:
            return self._hold_signal(sym, "rsi_warming_up", bar, selection, current_net)
        rsi = _rsi(closes[-(self.reversion_lookback + 1):])
        metrics = self._base_metrics(sym, bar, selection, current_net, {"rsi": rsi})
        if current_net == 0:
            if rsi >= self.reversion_rsi_high:
                return self._entry_signal(sym, -1, "high_vol_rsi_short", selection, metrics)
            if rsi <= self.reversion_rsi_low:
                return self._entry_signal(sym, 1, "high_vol_rsi_long", selection, metrics)
        elif current_net > 0 and rsi >= 50.0:
            return self._exit_signal(sym, "rsi_midline_exit", metrics)
        elif current_net < 0 and rsi <= 50.0:
            return self._exit_signal(sym, "rsi_midline_exit", metrics)
        return {"signal": None, "reason": "reversion_hold", "metrics": metrics}

    def _reversion_donchian_fade(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        history = list(self.reversion_history[sym])
        if len(history) < self.reversion_lookback:
            return self._hold_signal(sym, "donchian_fade_warming_up", bar, selection, current_net)
        sample = history[-self.reversion_lookback:]
        upper = max(item.high for item in sample)
        lower = min(item.low for item in sample)
        mid = (upper + lower) / 2.0
        buffer = 2.0 * self._tick_size(sym)
        metrics = self._base_metrics(sym, bar, selection, current_net, {"upper": upper, "lower": lower, "mid": mid})
        if current_net == 0:
            if bar.close >= upper - buffer:
                return self._entry_signal(sym, -1, "high_vol_donchian_fade_short", selection, metrics)
            if bar.close <= lower + buffer:
                return self._entry_signal(sym, 1, "high_vol_donchian_fade_long", selection, metrics)
        elif current_net > 0 and bar.close >= mid:
            return self._exit_signal(sym, "donchian_fade_mid_exit", metrics)
        elif current_net < 0 and bar.close <= mid:
            return self._exit_signal(sym, "donchian_fade_mid_exit", metrics)
        return {"signal": None, "reason": "reversion_hold", "metrics": metrics}

    def _reversion_atr_fade(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        history = list(self.reversion_history[sym])
        if len(history) < max(self.reversion_lookback, self.trend_atr_period + 1):
            return self._hold_signal(sym, "atr_fade_warming_up", bar, selection, current_net)
        atr = _atr(history[-(self.trend_atr_period + 1):])
        start_price = history[-min(6, len(history))].close
        impulse = bar.close - start_price
        metrics = self._base_metrics(sym, bar, selection, current_net, {"atr": atr, "impulse": impulse})
        if atr <= 0:
            return {"signal": None, "reason": "zero_atr", "metrics": metrics}
        if current_net == 0:
            if impulse >= self.reversion_atr_mult * atr:
                return self._entry_signal(sym, -1, "high_vol_atr_fade_short", selection, metrics)
            if impulse <= -self.reversion_atr_mult * atr:
                return self._entry_signal(sym, 1, "high_vol_atr_fade_long", selection, metrics)

        return {"signal": None, "reason": "reversion_hold", "metrics": metrics}

    def _trend_ma_cross(
        self,
        sym: str,
        bar: _AggBar,
        selection: _SelectionEntry,
        current_net: int,
        use_ema: bool,
    ) -> dict | None:
        closes = [item.close for item in self.trend_history[sym]] + [bar.close]
        if len(closes) < self.trend_slow_window:
            return self._hold_signal(sym, "trend_ma_warming_up", bar, selection, current_net)
        if use_ema:
            fast = _ema(closes[-self.trend_slow_window:], self.trend_fast_window)
            slow = _ema(closes[-self.trend_slow_window:], self.trend_slow_window)
        else:
            fast = sum(closes[-self.trend_fast_window:]) / self.trend_fast_window
            slow = sum(closes[-self.trend_slow_window:]) / self.trend_slow_window
        direction = 1 if fast > slow else -1 if fast < slow else 0
        metrics = self._base_metrics(sym, bar, selection, current_net, {"fast_ma": fast, "slow_ma": slow})
        if direction == 0:
            return {"signal": None, "reason": "trend_ma_neutral", "metrics": metrics}
        if current_net == 0 or (self.trend_exit_mode == "model" and current_net * direction < 0):
            return self._entry_signal(sym, direction, "low_vol_ma_trend", selection, metrics)
        return {"signal": None, "reason": "trend_hold", "metrics": metrics}

    def _trend_donchian(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        history = list(self.trend_history[sym])
        if len(history) < self.trend_donchian_window:
            return self._hold_signal(sym, "trend_donchian_warming_up", bar, selection, current_net)
        sample = history[-self.trend_donchian_window:]
        upper = max(item.high for item in sample)
        lower = min(item.low for item in sample)
        mid = (upper + lower) / 2.0
        buffer = 2.0 * self._tick_size(sym)
        metrics = self._base_metrics(sym, bar, selection, current_net, {"upper": upper, "lower": lower, "mid": mid})
        if current_net == 0:
            if bar.close > upper + buffer:
                return self._entry_signal(sym, 1, "low_vol_donchian_breakout_long", selection, metrics)
            if bar.close < lower - buffer:
                return self._entry_signal(sym, -1, "low_vol_donchian_breakout_short", selection, metrics)
        elif current_net > 0 and self.trend_exit_mode == "model" and self.trend_exit_on_midline and bar.close < mid:
            return self._exit_signal(sym, "donchian_midline_exit", metrics)
        elif current_net < 0 and self.trend_exit_mode == "model" and self.trend_exit_on_midline and bar.close > mid:
            return self._exit_signal(sym, "donchian_midline_exit", metrics)
        return {"signal": None, "reason": "trend_hold", "metrics": metrics}

    def _trend_atr_breakout(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict | None:
        history = list(self.trend_history[sym])
        need = max(self.trend_atr_period + 1, self.trend_slow_window)
        if len(history) < need:
            return self._hold_signal(sym, "trend_atr_warming_up", bar, selection, current_net)
        closes = [item.close for item in history[-self.trend_slow_window:]]
        mean = sum(closes) / len(closes)
        atr = _atr(history[-(self.trend_atr_period + 1):])
        upper = mean + self.trend_atr_mult * atr
        lower = mean - self.trend_atr_mult * atr
        metrics = self._base_metrics(sym, bar, selection, current_net, {"mean": mean, "atr": atr, "upper": upper, "lower": lower})
        if atr <= 0:
            return {"signal": None, "reason": "zero_trend_atr", "metrics": metrics}
        if current_net == 0:
            if bar.close > upper:
                return self._entry_signal(sym, 1, "low_vol_atr_breakout_long", selection, metrics)
            if bar.close < lower:
                return self._entry_signal(sym, -1, "low_vol_atr_breakout_short", selection, metrics)
        elif current_net > 0 and self.trend_exit_mode == "model" and bar.close < mean:
            return self._exit_signal(sym, "atr_breakout_mean_exit", metrics)
        elif current_net < 0 and self.trend_exit_mode == "model" and bar.close > mean:
            return self._exit_signal(sym, "atr_breakout_mean_exit", metrics)
        return {"signal": None, "reason": "trend_hold", "metrics": metrics}

    def _entry_signal(self, sym: str, direction: int, reason: str, selection: _SelectionEntry, metrics: dict) -> dict:
        if direction > 0 and not self.allow_long:
            return {"signal": None, "reason": "long_disabled", "metrics": metrics}
        if direction < 0 and not self.allow_short:
            return {"signal": None, "reason": "short_disabled", "metrics": metrics}
        if self._entry_limit_reached(sym):
            return {"signal": None, "reason": "daily_entry_limit", "metrics": metrics}
        self._register_entry(sym)
        if selection.regime == "reversion":
            self.entry_bar_count[sym] = self.reversion_bar_count[sym]
        else:
            self.entry_bar_count[sym] = self.trend_bar_count[sym]
        return {
            "signal": int(direction),
            "position_mode": "target",
            "target_margin_pct": selection.margin_pct,
            "signal_score": float(direction) * selection.confidence,
            "reason": reason,
            "metrics": metrics,
        }

    @staticmethod
    def _exit_signal(sym: str, reason: str, metrics: dict) -> dict:
        return {
            "signal": 0,
            "position_mode": "flat",
            "reason": reason,
            "metrics": metrics,
        }

    def _flat_signal(self, sym: str, reason: str, price: float, current_net: int) -> dict:
        return {
            "signal": 0,
            "position_mode": "flat",
            "reason": reason,
            "metrics": {
                "price": price,
                "current_net": current_net,
                "active_rebalance_key": self.active_rebalance_key,
            },
        }

    def _hold_signal(self, sym: str, reason: str, bar: _AggBar, selection: _SelectionEntry, current_net: int) -> dict:
        return {
            "signal": None,
            "reason": reason,
            "metrics": self._base_metrics(sym, bar, selection, current_net, {}),
        }

    def _base_metrics(self, sym: str, bar: _AggBar, selection: _SelectionEntry, current_net: int, extra: dict) -> dict:
        metrics = {
            "price": bar.close,
            "bar_start": bar.start,
            "bar_end": bar.end,
            "current_net": current_net,
            "regime": selection.regime,
            "vol_percentile": selection.percentile,
            "volatility": selection.volatility,
            "regime_confidence": selection.confidence,
            "target_margin_pct": selection.margin_pct,
            "selection_rank": selection.rank,
            "avg_daily_notional": selection.avg_daily_notional,
            "margin_rate": selection.margin_rate,
            "reversion_model": self.reversion_model,
            "trend_model": self.trend_model,
            "trend_exit_mode": self.trend_exit_mode,
        }
        metrics.update(extra)
        return metrics

    def _entry_limit_reached(self, sym: str) -> bool:
        if self.max_entries_per_symbol_per_day is None:
            return False
        current_date = self.current_time.date()
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        return self.entry_count[sym] >= self.max_entries_per_symbol_per_day

    def _register_entry(self, sym: str):
        if self.max_entries_per_symbol_per_day is None:
            return
        current_date = self.current_time.date()
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        self.entry_count[sym] += 1

    def _tick_size(self, sym: str) -> float:
        meta = self.account.fee_model._get_meta_data(pure_product_code(sym))
        return float(meta.get("tick_size", 1.0))

    @staticmethod
    def _normalize_symbol_set(value: Iterable[str] | str | None) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            items = value.replace(";", ",").split(",")
        else:
            items = list(value)
        return {pure_product_code(str(item).strip()).lower() for item in items if str(item).strip()}


class DailyVolatilityRegimeSwitchStrategy(VolatilityRegimeSwitchStrategy):
    """
    Daily-bar version of :class:`VolatilityRegimeSwitchStrategy`.

    The minute strategy uses 30-minute bars for high-volatility reversion and
    60-minute bars for low-volatility trend following. This class keeps the same
    regime contract and signal models, but evaluates them directly on completed
    daily bars:

    - volatility percentile >= threshold: use the reversion leg;
    - volatility percentile <= 1 - threshold: use the trend leg;
    - middle regime: stay flat.

    It deliberately disables top-N/performance selection by default. Every
    product with enough history and a clear volatility regime is tradable, and
    the total target margin is spread across the active products.
    """

    def __init__(self, *args, selection_count: int | None = 0, **kwargs):
        # The daily baseline is intended to test the full regime logic before
        # adding any rolling product-selection layer.
        if selection_count in (None, 0):
            selection_count = 10000
        kwargs["use_performance_selection"] = False
        kwargs.setdefault("min_selected_confidence", 0.0)
        kwargs.setdefault("exploration_count", 0)
        super().__init__(*args, selection_count=int(selection_count), **kwargs)

    def on_init(self):
        GeneralSignalStrategy.on_init(self)
        print(
            "[Strategy DailyVolRegime] "
            f"symbols={len(self.symbols)} | threshold={self.regime_threshold:g} | "
            f"reversion={self.reversion_model} | trend={self.trend_model} | "
            f"margin_target={self.total_margin_target:g} | no_topn_selection=True"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        daily_bars = self._update_daily_bar_state(bar_data)
        self._maybe_reselect_universe(self.current_time.date())

        signals: dict[str, dict] = {}
        for sym, daily_bar in daily_bars.items():
            self.reversion_bar_count[sym] += 1
            self.trend_bar_count[sym] += 1
            self.trend_exit_snapshots[sym] = self.trend_macd_trackers[sym].update(
                daily_bar,
                self.trend_bar_count[sym],
            )

            selection = self.active_selection.get(sym)
            current_net = self.get_net_position(sym)

            if sym in self.force_flat_symbols:
                if current_net == 0:
                    self.force_flat_symbols.discard(sym)
                else:
                    signals[sym] = self._flat_signal(sym, "daily_regime_change", daily_bar.close, current_net)
                    self._append_daily_bar(sym, daily_bar)
                    continue

            if selection is None:
                if current_net != 0:
                    signals[sym] = self._flat_signal(
                        sym,
                        "daily_middle_or_insufficient_regime",
                        daily_bar.close,
                        current_net,
                    )
                self._append_daily_bar(sym, daily_bar)
                continue

            if selection.regime == "reversion":
                signal = self._reversion_signal(sym, daily_bar, selection)
            else:
                signal = self._trend_signal(sym, daily_bar, selection)
            if signal is not None:
                signals[sym] = signal

            self._append_daily_bar(sym, daily_bar)

        return signals

    def _update_daily_bar_state(self, bar_data: dict) -> dict[str, _AggBar]:
        daily_bars: dict[str, _AggBar] = {}
        daily_hist_size = max(
            self.vol_window_days + self.vol_percentile_lookback_days + 10,
            self.vol_window_days + self.min_vol_percentile_samples + 10,
            90,
        )
        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not bar:
                continue
            close = bar.get("close")
            if close is None or pd.isna(close):
                continue

            close = float(close)
            if close <= 0:
                continue
            open_price = _float_or(bar.get("open"), close)
            high = _float_or(bar.get("high"), close)
            low = _float_or(bar.get("low"), close)
            volume = _float_or(bar.get("volume"), 0.0)
            daily_bars[sym] = _AggBar(self.current_time, self.current_time, open_price, high, low, close, volume)

            self.daily_close_history.setdefault(sym, deque(maxlen=daily_hist_size)).append(close)
            self.daily_notional_history.setdefault(sym, deque(maxlen=daily_hist_size)).append(
                volume * close * self.contract_multipliers.get(sym, 1.0)
            )
        return daily_bars

    def _append_daily_bar(self, sym: str, daily_bar: _AggBar):
        self.reversion_history[sym].append(daily_bar)
        self.trend_history[sym].append(daily_bar)

    def _select_candidates(self, candidates: list[dict]) -> list[dict]:
        if not candidates:
            return []
        return sorted(candidates, key=lambda row: row["distance"], reverse=True)

    def _is_performance_rejected(self, performance: dict) -> bool:
        return False


class DailyVolatilityTrendFactorStrategy(GeneralSignalStrategy):
    """
    Daily volatility-filtered trend factor strategy.

    This is the daily version of the volatility-regime research idea. It does
    not trade a separate high-volatility reversion leg. Instead, it:

    1. Calculates each product's realized-volatility percentile from completed
       daily closes.
    2. Keeps only products in a configurable volatility percentile band.
    3. Scores trend direction with a small multi-factor trend model.
    4. Rebalances the selected universe on a daily/weekly/monthly schedule.
    5. Allocates target margin roughly equally across selected products.

    Signal timing follows the engine contract: the signal is generated after
    the current daily bar, then filled by the next daily bar.
    """

    DEFAULT_FACTOR_WEIGHTS = {
        "macd_hist_norm": 0.18,
        "aroon25": 0.16,
        "dynamic_score_signed": 0.14,
        "r2xslope_30": 0.12,
        "slope_bp_30": 0.10,
        "adx14_signed": 0.10,
        "ma21_55_spread": 0.08,
        "vol_adj_mom20": 0.07,
        "er20_signed": 0.05,
    }

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        vol_window_days: int = 20,
        vol_percentile_lookback_days: int = 252,
        min_vol_percentile_samples: int = 120,
        min_trade_vol_pct: float = 0.70,
        max_trade_vol_pct: float = 0.90,
        selection_count: int = 15,
        rebalance_frequency: str = "weekly",
        total_margin_target: float = 0.30,
        max_symbol_margin_pct: float | None = 0.03,
        max_symbol_notional_pct: float | None = 2.0,
        entry_score_threshold: float = 0.20,
        exit_score_threshold: float = 0.05,
        min_selection_abs_score: float = 0.10,
        feature_z_window: int = 252,
        feature_z_min: int = 60,
        min_active_factors: int = 4,
        quality_weight: float = 0.25,
        min_avg_daily_notional: float = 0.0,
        excluded_symbols: Iterable[str] | str | None = None,
        factor_weights: dict | None = None,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.vol_window_days = int(vol_window_days)
        self.vol_percentile_lookback_days = int(vol_percentile_lookback_days)
        self.min_vol_percentile_samples = int(min_vol_percentile_samples)
        self.min_trade_vol_pct = float(min_trade_vol_pct)
        self.max_trade_vol_pct = float(max_trade_vol_pct)
        self.selection_count = int(selection_count)
        self.rebalance_frequency = str(rebalance_frequency).strip().lower()
        self.total_margin_target = float(total_margin_target)
        self.max_symbol_margin_pct = None if max_symbol_margin_pct in (None, 0, "") else float(max_symbol_margin_pct)
        self.max_symbol_notional_pct = None if max_symbol_notional_pct in (None, 0, "") else float(max_symbol_notional_pct)
        self.entry_score_threshold = float(entry_score_threshold)
        self.exit_score_threshold = float(exit_score_threshold)
        self.min_selection_abs_score = float(min_selection_abs_score)
        self.feature_z_window = int(feature_z_window)
        self.feature_z_min = int(feature_z_min)
        self.min_active_factors = int(min_active_factors)
        self.quality_weight = float(quality_weight)
        self.min_avg_daily_notional = float(min_avg_daily_notional)
        self.excluded_symbols = self._normalize_symbol_set(excluded_symbols)
        self.factor_weights = self._normalize_factor_weights(factor_weights or self.DEFAULT_FACTOR_WEIGHTS)

        if self.vol_window_days < 5:
            raise ValueError("vol_window_days must be at least 5")
        if self.vol_percentile_lookback_days < 1:
            raise ValueError("vol_percentile_lookback_days must be positive")
        if not 0.0 <= self.min_trade_vol_pct <= self.max_trade_vol_pct <= 1.0:
            raise ValueError("trade volatility percentile band must satisfy 0 <= min <= max <= 1")
        if self.selection_count <= 0:
            raise ValueError("selection_count must be positive")
        if self.rebalance_frequency not in {"daily", "weekly", "monthly"}:
            raise ValueError("rebalance_frequency must be daily, weekly, or monthly")
        if self.total_margin_target <= 0:
            raise ValueError("total_margin_target must be positive")

        self.contract_multipliers: dict[str, float] = {}
        self.margin_rates: dict[str, float] = {}
        for sym in self.symbols:
            meta = self.account.fee_model._get_meta_data(pure_product_code(sym))
            self.contract_multipliers[sym] = float(meta.get("multiplier", 1.0))
            self.margin_rates[sym] = float(meta.get("margin_rate", 0.1))

        history_size = max(
            self.vol_window_days + self.vol_percentile_lookback_days + 30,
            self.feature_z_window + 160,
            520,
        )
        self.daily_history: dict[str, Deque[_AggBar]] = {
            sym: deque(maxlen=history_size) for sym in self.symbols
        }
        self.daily_notional_history: dict[str, Deque[float]] = {
            sym: deque(maxlen=history_size) for sym in self.symbols
        }
        self.factor_value_history: dict[str, dict[str, Deque[float]]] = {
            sym: {name: deque(maxlen=self.feature_z_window) for name in self.factor_weights}
            for sym in self.symbols
        }
        self.latest_stats: dict[str, dict] = {}
        self.active_rebalance_key = None
        self.active_selection: dict[str, _DailyTrendSelection] = {}
        self.selection_records: list[dict] = []

    def on_init(self):
        super().on_init()
        print(
            "[Strategy DailyVolTrendFactor] "
            f"symbols={len(self.symbols)} | vol_band={self.min_trade_vol_pct:.0%}-{self.max_trade_vol_pct:.0%} | "
            f"selection_count={self.selection_count} | rebalance={self.rebalance_frequency} | "
            f"margin_target={self.total_margin_target:.1%} | entry_score={self.entry_score_threshold:g}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        self._update_daily_inputs(bar_data)
        self._maybe_reselect_universe(self.current_time.date())

        signals: dict[str, dict] = {}
        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not self._bar_is_tradeable(bar):
                continue

            close = float(bar["close"])
            current_net = self.get_net_position(sym)
            selection = self.active_selection.get(sym)
            stats = self.latest_stats.get(sym)

            if selection is None:
                if current_net != 0:
                    signals[sym] = self._flat_signal(sym, "daily_trend_not_selected", close, current_net, stats)
                continue

            if not stats or not self._stats_in_trade_band(stats):
                if current_net != 0:
                    signals[sym] = self._flat_signal(sym, "daily_trend_filter_failed", close, current_net, stats)
                continue

            score = float(stats.get("trend_score", math.nan))
            if not math.isfinite(score):
                if current_net != 0:
                    signals[sym] = self._flat_signal(sym, "daily_trend_score_missing", close, current_net, stats)
                continue

            direction = self._target_direction(score, current_net)
            if direction == 0:
                if current_net != 0:
                    signals[sym] = self._flat_signal(sym, "daily_trend_score_exit", close, current_net, stats)
                continue

            signals[sym] = {
                "signal": direction,
                "position_mode": "target",
                "target_margin_pct": selection.margin_pct,
                "signal_score": score,
                "reason": "daily_trend_factor_long" if direction > 0 else "daily_trend_factor_short",
                "metrics": self._signal_metrics(sym, close, current_net, selection, stats),
            }
        return signals

    def _update_daily_inputs(self, bar_data: dict):
        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not self._bar_is_tradeable(bar):
                continue
            close = float(bar["close"])
            open_price = _float_or(bar.get("open"), close)
            high = _float_or(bar.get("high"), close)
            low = _float_or(bar.get("low"), close)
            volume = _float_or(bar.get("volume"), 0.0)
            daily_bar = _AggBar(self.current_time, self.current_time, open_price, high, low, close, volume)
            self.daily_history[sym].append(daily_bar)
            self.daily_notional_history[sym].append(volume * close * self.contract_multipliers.get(sym, 1.0))

            raw_factors, diagnostics = self._compute_raw_factors(sym)
            zscores = self._update_factor_zscores(sym, raw_factors)
            trend_score, active_factor_count = self._compose_trend_score(zscores)
            vol_stats = self._volatility_stats(sym)
            self.latest_stats[sym] = {
                **diagnostics,
                **vol_stats,
                "raw_factors": raw_factors,
                "factor_zscores": zscores,
                "trend_score": trend_score,
                "active_factor_count": active_factor_count,
                "avg_daily_notional": self._average_daily_notional(sym),
            }

    def _maybe_reselect_universe(self, trade_date):
        rebalance_key = self._rebalance_key(trade_date)
        if self.active_rebalance_key == rebalance_key:
            return
        selection = self._build_selection()
        self.active_selection = selection
        self.active_rebalance_key = rebalance_key
        self._record_selection_snapshot(trade_date, rebalance_key, selection)
        print(
            f"[Strategy DailyVolTrendFactor] {trade_date} selected={len(selection)} | "
            f"{', '.join(list(selection)[:12])}{'...' if len(selection) > 12 else ''}"
        )

    def _build_selection(self) -> dict[str, _DailyTrendSelection]:
        candidates = []
        center = (self.min_trade_vol_pct + self.max_trade_vol_pct) / 2.0
        half_width = max((self.max_trade_vol_pct - self.min_trade_vol_pct) / 2.0, 1e-9)
        for sym, stats in self.latest_stats.items():
            if sym in self.excluded_symbols:
                continue
            if not self._stats_in_trade_band(stats):
                continue
            score = float(stats.get("trend_score", math.nan))
            if not math.isfinite(score):
                continue
            abs_score = abs(score)
            if abs_score < self.min_selection_abs_score:
                continue
            avg_notional = float(stats.get("avg_daily_notional", 0.0) or 0.0)
            if self.min_avg_daily_notional > 0 and avg_notional < self.min_avg_daily_notional:
                continue
            factor_count = int(stats.get("active_factor_count", 0) or 0)
            if factor_count < self.min_active_factors:
                continue

            pct = float(stats["vol_percentile"])
            vol_confidence = max(0.0, 1.0 - abs(pct - center) / half_width)
            quality = max(0.0, min(1.0, float(stats.get("trend_quality", 0.0) or 0.0)))
            selection_score = abs_score * (1.0 + self.quality_weight * quality) * (1.0 + 0.15 * vol_confidence)
            candidates.append({
                "sym": sym,
                "stats": stats,
                "abs_score": abs_score,
                "selection_score": selection_score,
            })

        candidates.sort(key=lambda row: row["selection_score"], reverse=True)
        selected = candidates[: self.selection_count]
        if not selected:
            return {}

        base_margin = self.total_margin_target / max(1, len(selected))
        selection: dict[str, _DailyTrendSelection] = {}
        for rank, row in enumerate(selected, start=1):
            sym = row["sym"]
            stats = row["stats"]
            margin_rate = self.margin_rates.get(sym, 0.1)
            margin_pct = self._apply_margin_caps(base_margin, margin_rate)
            selection[sym] = _DailyTrendSelection(
                percentile=float(stats["vol_percentile"]),
                volatility=float(stats["volatility"]),
                trend_score=float(stats["trend_score"]),
                abs_score=float(row["abs_score"]),
                trend_quality=float(stats.get("trend_quality", 0.0) or 0.0),
                margin_pct=float(margin_pct),
                rank=rank,
                avg_daily_notional=float(stats.get("avg_daily_notional", 0.0) or 0.0),
                margin_rate=float(margin_rate),
                selection_score=float(row["selection_score"]),
            )
        return selection

    def _target_direction(self, score: float, current_net: int) -> int:
        if score >= self.entry_score_threshold:
            return 1
        if score <= -self.entry_score_threshold:
            return -1
        if current_net > 0 and score > self.exit_score_threshold:
            return 1
        if current_net < 0 and score < -self.exit_score_threshold:
            return -1
        return 0

    def _stats_in_trade_band(self, stats: dict | None) -> bool:
        if not stats:
            return False
        pct = stats.get("vol_percentile")
        if pct is None or pd.isna(pct) or not math.isfinite(float(pct)):
            return False
        return self.min_trade_vol_pct <= float(pct) <= self.max_trade_vol_pct

    def _volatility_stats(self, sym: str) -> dict:
        closes = [bar.close for bar in self.daily_history.get(sym, [])]
        vol_series = _rolling_volatility_series(closes, self.vol_window_days)
        if len(vol_series) < self.min_vol_percentile_samples + 1:
            return {"volatility": math.nan, "vol_percentile": math.nan}
        current_vol = float(vol_series[-1])
        history_vols = vol_series[:-1]
        if self.vol_percentile_lookback_days > 0:
            history_vols = history_vols[-self.vol_percentile_lookback_days:]
        if len(history_vols) < self.min_vol_percentile_samples:
            return {"volatility": current_vol, "vol_percentile": math.nan}
        return {
            "volatility": current_vol,
            "vol_percentile": _percentile_rank(current_vol, history_vols),
        }

    def _compute_raw_factors(self, sym: str) -> tuple[dict[str, float], dict]:
        history = list(self.daily_history.get(sym, []))
        if len(history) < 30:
            return {}, {"trend_quality": 0.0}

        df = pd.DataFrame({
            "open": [bar.open for bar in history],
            "high": [bar.high for bar in history],
            "low": [bar.low for bar in history],
            "close": [bar.close for bar in history],
            "volume": [bar.volume for bar in history],
        })
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        open_price = df["open"].astype(float)
        returns = close.pct_change()

        raw: dict[str, float] = {}
        diagnostics: dict[str, float] = {}

        slope_bp, r2_smooth = self._latest_slope_and_r2(close)
        raw["slope_bp_30"] = slope_bp
        raw["r2xslope_30"] = r2_smooth * slope_bp if math.isfinite(r2_smooth) and math.isfinite(slope_bp) else math.nan
        diagnostics["r2_smooth"] = r2_smooth

        raw["er20_signed"], er20_abs = self._latest_er_signed(close, 20)
        diagnostics["er20_abs"] = er20_abs

        adx_signed, adx_value = self._latest_adx_signed(high, low, close)
        raw["adx14_signed"] = adx_signed
        diagnostics["adx14"] = adx_value

        chop_value = self._latest_chop(high, low, close, 14)
        raw["chop14_trend"] = (50.0 - chop_value) * _sign_or_nan(slope_bp) if math.isfinite(chop_value) else math.nan
        diagnostics["chop14"] = chop_value

        raw["ma21_55_spread"] = self._ma_spread(close, 21, 55)
        raw["ma21_144_spread"] = self._ma_spread(close, 21, 144)
        raw["vol_adj_mom20"] = self._vol_adj_momentum(close, returns, 20)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        raw["macd_hist_norm"] = _safe_last((macd - macd_signal) / close.replace(0, math.nan))
        raw["aroon25"] = self._latest_aroon(high, low, 25)
        raw["dynamic_score_signed"] = self._latest_dynamic_score(close, open_price, slope_bp)
        raw["er20_signed"] = raw.get("er20_signed", math.nan)

        quality_items = [
            max(0.0, min(1.0, r2_smooth if math.isfinite(r2_smooth) else 0.0)),
            max(0.0, min(1.0, er20_abs if math.isfinite(er20_abs) else 0.0)),
            max(0.0, min(1.0, adx_value / 50.0 if math.isfinite(adx_value) else 0.0)),
            max(0.0, min(1.0, (50.0 - chop_value) / 50.0 if math.isfinite(chop_value) else 0.0)),
        ]
        diagnostics["trend_quality"] = sum(quality_items) / len(quality_items)
        return raw, diagnostics

    def _update_factor_zscores(self, sym: str, raw_factors: dict[str, float]) -> dict[str, float]:
        zscores: dict[str, float] = {}
        history_by_factor = self.factor_value_history.setdefault(
            sym, {name: deque(maxlen=self.feature_z_window) for name in self.factor_weights}
        )
        for name in self.factor_weights:
            value = raw_factors.get(name, math.nan)
            value_is_valid = value is not None and not pd.isna(value) and math.isfinite(float(value))
            sample = list(history_by_factor.setdefault(name, deque(maxlen=self.feature_z_window)))
            zscores[name] = math.nan
            if value_is_valid and len(sample) >= self.feature_z_min:
                mean = sum(sample) / len(sample)
                sigma = _std([float(item) for item in sample])
                if sigma > 0:
                    zscores[name] = max(-5.0, min(5.0, (float(value) - mean) / sigma))
            if value_is_valid:
                history_by_factor[name].append(float(value))
        return zscores

    def _compose_trend_score(self, zscores: dict[str, float]) -> tuple[float, int]:
        weighted = 0.0
        weight_sum = 0.0
        active = 0
        for name, weight in self.factor_weights.items():
            value = zscores.get(name, math.nan)
            if value is None or pd.isna(value) or not math.isfinite(float(value)):
                continue
            weighted += float(weight) * float(value)
            weight_sum += abs(float(weight))
            active += 1
        if active < self.min_active_factors or weight_sum <= 0:
            return math.nan, active
        return weighted / weight_sum, active

    def _record_selection_snapshot(self, trade_date, rebalance_key, selection: dict[str, _DailyTrendSelection]):
        self.selection_records.append({
            "datetime": self.current_time,
            "trade_date": trade_date,
            "rebalance_key": str(rebalance_key),
            "record_type": "summary",
            "selected_count": len(selection),
            "vol_band": f"{self.min_trade_vol_pct:.2f}-{self.max_trade_vol_pct:.2f}",
        })
        for sym, entry in sorted(selection.items(), key=lambda item: item[1].rank):
            self.selection_records.append({
                "datetime": self.current_time,
                "trade_date": trade_date,
                "rebalance_key": str(rebalance_key),
                "record_type": "symbol",
                "symbol": sym,
                "regime": "trend",
                "vol_percentile": entry.percentile,
                "volatility": entry.volatility,
                "trend_score": entry.trend_score,
                "abs_score": entry.abs_score,
                "trend_quality": entry.trend_quality,
                "target_margin_pct": entry.margin_pct,
                "rank": entry.rank,
                "avg_daily_notional": entry.avg_daily_notional,
                "margin_rate": entry.margin_rate,
                "selection_score": entry.selection_score,
            })

    def _signal_metrics(
        self,
        sym: str,
        close: float,
        current_net: int,
        selection: _DailyTrendSelection,
        stats: dict,
    ) -> dict:
        return {
            "price": close,
            "current_net": current_net,
            "regime": "trend",
            "vol_percentile": stats.get("vol_percentile"),
            "volatility": stats.get("volatility"),
            "trend_score": stats.get("trend_score"),
            "trend_quality": stats.get("trend_quality"),
            "active_factor_count": stats.get("active_factor_count"),
            "target_margin_pct": selection.margin_pct,
            "selection_rank": selection.rank,
            "avg_daily_notional": stats.get("avg_daily_notional"),
            "r2_smooth": stats.get("r2_smooth"),
            "er20_abs": stats.get("er20_abs"),
            "adx14": stats.get("adx14"),
            "chop14": stats.get("chop14"),
        }

    def _flat_signal(self, sym: str, reason: str, price: float, current_net: int, stats: dict | None) -> dict:
        return {
            "signal": 0,
            "position_mode": "flat",
            "reason": reason,
            "metrics": {
                "price": price,
                "current_net": current_net,
                "vol_percentile": None if not stats else stats.get("vol_percentile"),
                "trend_score": None if not stats else stats.get("trend_score"),
                "active_rebalance_key": self.active_rebalance_key,
            },
        }

    def _rebalance_key(self, trade_date):
        if self.rebalance_frequency == "daily":
            return ("daily", trade_date)
        if self.rebalance_frequency == "weekly":
            iso_year, iso_week, _ = trade_date.isocalendar()
            return ("weekly", iso_year, iso_week)
        return ("monthly", trade_date.year, trade_date.month)

    def _average_daily_notional(self, sym: str) -> float:
        history = list(self.daily_notional_history.get(sym, []))
        valid = [float(item) for item in history[-self.vol_window_days:] if item and item > 0]
        if not valid:
            return 0.0
        return sum(valid) / len(valid)

    def _apply_margin_caps(self, margin_pct: float, margin_rate: float) -> float:
        capped = float(margin_pct)
        if self.max_symbol_margin_pct is not None:
            capped = min(capped, self.max_symbol_margin_pct)
        if self.max_symbol_notional_pct is not None:
            capped = min(capped, float(self.max_symbol_notional_pct) * float(margin_rate))
        return max(0.0, capped)

    @staticmethod
    def _bar_is_tradeable(bar: dict | None) -> bool:
        if not bar:
            return False
        close = bar.get("close")
        return close is not None and not pd.isna(close) and float(close) > 0

    @staticmethod
    def _normalize_symbol_set(value: Iterable[str] | str | None) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            items = value.replace(";", ",").split(",")
        else:
            items = list(value)
        return {pure_product_code(str(item).strip()).lower() for item in items if str(item).strip()}

    @staticmethod
    def _normalize_factor_weights(weights: dict) -> dict[str, float]:
        cleaned = {}
        for name, weight in (weights or {}).items():
            if weight is None or pd.isna(weight):
                continue
            numeric = float(weight)
            if numeric != 0:
                cleaned[str(name)] = numeric
        if not cleaned:
            raise ValueError("factor_weights cannot be empty")
        return cleaned

    @staticmethod
    def _latest_slope_and_r2(close: pd.Series) -> tuple[float, float]:
        if len(close) < 34:
            return math.nan, math.nan
        log_close = [math.log(float(item)) if item > 0 else math.nan for item in close.tolist()]
        r2_values = []
        slope = math.nan
        for end in range(len(log_close) - 4, len(log_close) + 1):
            sample = log_close[end - 30:end]
            local_slope, local_r2 = _linear_fit(sample)
            if math.isfinite(local_r2):
                r2_values.append(local_r2)
            if end == len(log_close):
                slope = local_slope
        r2_smooth = sum(r2_values) / len(r2_values) if r2_values else math.nan
        slope_bp = slope * 10000.0 if math.isfinite(slope) else math.nan
        return slope_bp, r2_smooth

    @staticmethod
    def _latest_er_signed(close: pd.Series, window: int) -> tuple[float, float]:
        if len(close) < window + 1:
            return math.nan, math.nan
        sample = close.tail(window + 1).astype(float).tolist()
        path = sum(abs(b - a) for a, b in zip(sample, sample[1:]))
        displacement = abs(sample[-1] - sample[0])
        if path <= 0:
            return math.nan, math.nan
        er = displacement / path
        direction = _sign_or_nan(sample[-1] - sample[0])
        return er * direction if math.isfinite(direction) else math.nan, er

    @staticmethod
    def _latest_adx_signed(high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[float, float]:
        if len(close) < 20:
            return math.nan, math.nan
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        hd = high - high.shift(1)
        ld = low.shift(1) - low
        dmp = pd.Series([hd.iloc[i] if hd.iloc[i] > 0 and hd.iloc[i] > ld.iloc[i] else 0.0 for i in range(len(hd))])
        dmm = pd.Series([ld.iloc[i] if ld.iloc[i] > 0 and ld.iloc[i] > hd.iloc[i] else 0.0 for i in range(len(ld))])
        tr_ema = tr.ewm(span=14, adjust=False).mean()
        pdi = dmp.ewm(span=14, adjust=False).mean() * 100.0 / tr_ema.replace(0, math.nan)
        mdi = dmm.ewm(span=14, adjust=False).mean() * 100.0 / tr_ema.replace(0, math.nan)
        dx = (mdi - pdi).abs() / (mdi + pdi).replace(0, math.nan) * 100.0
        adx = _safe_last(dx.ewm(span=14, adjust=False).mean())
        direction = _sign_or_nan(_safe_last(pdi) - _safe_last(mdi))
        return adx * direction if math.isfinite(adx) and math.isfinite(direction) else math.nan, adx

    @staticmethod
    def _latest_chop(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> float:
        if len(close) < window + 1:
            return math.nan
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        sum_tr = float(tr.tail(window).sum())
        max_h = float(high.tail(window).max())
        min_l = float(low.tail(window).min())
        if sum_tr <= 0 or max_h <= min_l:
            return math.nan
        return 100.0 * math.log10(sum_tr / (max_h - min_l)) / math.log10(window)

    @staticmethod
    def _ma_spread(close: pd.Series, short: int, long: int) -> float:
        if len(close) < long:
            return math.nan
        short_ma = float(close.tail(short).mean())
        long_ma = float(close.tail(long).mean())
        if long_ma <= 0:
            return math.nan
        return short_ma / long_ma - 1.0

    @staticmethod
    def _vol_adj_momentum(close: pd.Series, returns: pd.Series, window: int) -> float:
        if len(close) < window + 1:
            return math.nan
        vol = float(returns.tail(window).std(ddof=0))
        if vol <= 0 or not math.isfinite(vol):
            return math.nan
        return float(close.iloc[-1] / close.iloc[-window - 1] - 1.0) / (vol * math.sqrt(window))

    @staticmethod
    def _latest_aroon(high: pd.Series, low: pd.Series, window: int) -> float:
        if len(high) < window:
            return math.nan
        highs = high.tail(window).tolist()
        lows = low.tail(window).tolist()
        high_pos = max(range(window), key=lambda idx: highs[idx])
        low_pos = min(range(window), key=lambda idx: lows[idx])
        return ((high_pos + 1.0) - (low_pos + 1.0)) / float(window)

    @staticmethod
    def _latest_dynamic_score(close: pd.Series, open_price: pd.Series, slope_bp: float) -> float:
        def score(window: int) -> float:
            if len(close) < window + 1:
                return math.nan
            close_tail = close.tail(window + 1)
            path = float(close_tail.diff().abs().tail(window).sum())
            displacement = abs(float(close_tail.iloc[-1] - close_tail.iloc[0]))
            body = (close - open_price).tail(window)
            body_sum = abs(float(body.sum()))
            body_path = float(body.abs().sum())
            if path <= 0 or body_path <= 0:
                return math.nan
            return (displacement / path) * (body_sum / body_path)

        values = [(8, 0.5), (20, 0.3), (55, 0.2)]
        total = 0.0
        weight_sum = 0.0
        for window, weight in values:
            value = score(window)
            if math.isfinite(value):
                total += weight * value
                weight_sum += weight
        if weight_sum <= 0:
            return math.nan
        direction = _sign_or_nan(slope_bp)
        return 100.0 * total / weight_sum * direction if math.isfinite(direction) else math.nan


def _linear_fit(values: list[float]) -> tuple[float, float]:
    clean = [float(item) for item in values]
    if len(clean) < 2 or any(pd.isna(item) or not math.isfinite(item) for item in clean):
        return math.nan, math.nan
    n = len(clean)
    x_mean = (n - 1) / 2.0
    y_mean = sum(clean) / n
    sxx = sum((idx - x_mean) ** 2 for idx in range(n))
    if sxx <= 0:
        return math.nan, math.nan
    sxy = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(clean))
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    fitted = [slope * idx + intercept for idx in range(n)]
    ss_res = sum((value - fit) ** 2 for value, fit in zip(clean, fitted))
    ss_tot = sum((value - y_mean) ** 2 for value in clean)
    r2 = 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    return slope, r2


def _safe_last(series: pd.Series) -> float:
    if series is None or len(series) == 0:
        return math.nan
    value = series.iloc[-1]
    if value is None or pd.isna(value):
        return math.nan
    return float(value)


def _sign_or_nan(value: float) -> float:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return math.nan
    numeric = float(value)
    if numeric > 0:
        return 1.0
    if numeric < 0:
        return -1.0
    return math.nan


def _float_or(value, default: float) -> float:
    if value is None or pd.isna(value):
        return float(default)
    return float(value)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((item - mean) ** 2 for item in values) / len(values)
    return math.sqrt(max(0.0, var))


def _rolling_volatility_series(closes: list[float], window: int) -> list[float]:
    if len(closes) < window + 1:
        return []
    returns = []
    for prev, current in zip(closes, closes[1:]):
        if prev > 0 and current > 0:
            returns.append(current / prev - 1.0)
        else:
            returns.append(math.nan)

    vol_series = []
    for end in range(window, len(returns) + 1):
        sample = returns[end - window:end]
        if any(pd.isna(item) for item in sample):
            continue
        vol = _std([float(item) for item in sample])
        if vol > 0 and math.isfinite(vol):
            vol_series.append(vol)
    return vol_series


def _percentile_rank(value: float, observations: list[float]) -> float:
    clean = [float(item) for item in observations if item is not None and not pd.isna(item) and math.isfinite(item)]
    if not clean:
        return math.nan
    less = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return (less + 0.5 * equal) / len(clean)


def _max_drawdown_from_returns(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for item in returns:
        equity += float(item)
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return max_drawdown


def _atr(history: list[_AggBar]) -> float:
    if len(history) < 2:
        return 0.0
    trs = []
    for prev, current in zip(history, history[1:]):
        trs.append(max(
            current.high - current.low,
            abs(current.high - prev.close),
            abs(current.low - prev.close),
        ))
    return sum(trs) / len(trs) if trs else 0.0


def _rsi(closes: list[float]) -> float:
    gains = []
    losses = []
    for prev, current in zip(closes, closes[1:]):
        delta = current - prev
        if delta >= 0:
            gains.append(delta)
        else:
            losses.append(abs(delta))
    avg_gain = sum(gains) / max(1, len(closes) - 1)
    avg_loss = sum(losses) / max(1, len(closes) - 1)
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _ema(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (float(window) + 1.0)
    value = float(values[0])
    for item in values[1:]:
        value = alpha * float(item) + (1.0 - alpha) * value
    return value


def _ema_step(previous: float, current: float, window: int) -> float:
    alpha = 2.0 / (float(window) + 1.0)
    return alpha * float(current) + (1.0 - alpha) * float(previous)


def tradable_research_symbols() -> list[str]:
    symbols = []
    for code in FEE_DICT:
        raw = pure_product_code(code)
        if raw not in symbols:
            symbols.append(raw)
    return sorted(symbols)
