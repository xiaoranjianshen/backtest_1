# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable

import pandas as pd

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


@dataclass
class _Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class _EntryState:
    direction: int
    price: float
    bar_index: int
    best_price: float


class DonchianATRBreakoutStrategy(GeneralSignalStrategy):
    """
    Donchian channel breakout strategy with ATR-based risk control.

    Signals use only completed historical bars plus the current close. The
    breakout channel is calculated from previous bars, so the current bar's
    high/low is not used to create its own breakout threshold.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        donchian_window: int = 144,
        atr_period: int = 72,
        trend_window: int = 240,
        breakout_buffer_ticks: float = 2.0,
        min_channel_atr: float = 1.8,
        max_extension_atr: float = 1.5,
        atr_stop_mult: float = 3.2,
        exit_on_midline: bool = True,
        max_hold_bars: int = 384,
        cooldown_bars: int = 18,
        max_entries_per_symbol_per_day: int | None = 3,
        allowed_entry_hours: Iterable[int] | str | None = "9,10,13,21,22,23,0,1,2",
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.donchian_window = int(donchian_window)
        self.atr_period = int(atr_period)
        self.trend_window = int(trend_window)
        self.breakout_buffer_ticks = float(breakout_buffer_ticks)
        self.min_channel_atr = float(min_channel_atr)
        self.max_extension_atr = float(max_extension_atr)
        self.atr_stop_mult = float(atr_stop_mult)
        self.exit_on_midline = bool(exit_on_midline)
        self.max_hold_bars = int(max_hold_bars)
        self.cooldown_bars = int(cooldown_bars)
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day in (None, "", 0) else int(max_entries_per_symbol_per_day)
        )
        self.allowed_entry_hours = self._parse_allowed_hours(allowed_entry_hours)

        if self.donchian_window < 2:
            raise ValueError("donchian_window must be at least 2")
        if self.atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if self.trend_window < 2:
            raise ValueError("trend_window must be at least 2")
        if self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars must be positive")

        history_size = max(self.donchian_window + 2, self.atr_period + 2, self.trend_window + 2)
        self.history: dict[str, Deque[_Bar]] = {sym: deque(maxlen=history_size) for sym in self.symbols}
        self.ema: dict[str, float | None] = {sym: None for sym in self.symbols}
        self.bar_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.entry_state: dict[str, _EntryState | None] = {sym: None for sym in self.symbols}
        self.last_exit_bar: dict[str, int | None] = {sym: None for sym in self.symbols}
        self.entry_count_date: dict[str, object | None] = {sym: None for sym in self.symbols}
        self.entry_count: dict[str, int] = {sym: 0 for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy DonchianATRBreakout] symbols={self.symbols} | "
            f"donchian={self.donchian_window} | atr={self.atr_period} | trend={self.trend_window}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        for sym in self.symbols:
            raw_bar = bar_data.get(sym)
            if not raw_bar:
                continue

            bar = self._coerce_bar(raw_bar)
            if bar is None:
                continue

            self.bar_count[sym] += 1
            snapshot = self._snapshot(sym, bar)
            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, bar.close)

            if current_net != 0:
                signals[sym] = self._position_signal(sym, bar.close, current_net, snapshot)
            else:
                signals[sym] = self._flat_signal(sym, bar.close, current_net, snapshot)

            self._append_bar(sym, bar)

        return signals

    def _flat_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        blocked = self._entry_block_reason(sym)
        if blocked:
            return self._hold_signal(blocked, price, current_net, snapshot)

        if not snapshot["ready"]:
            return self._hold_signal(snapshot["reason"], price, current_net, snapshot)

        upper = snapshot["upper"]
        lower = snapshot["lower"]
        ema = snapshot["ema"]
        atr = snapshot["atr"]
        tick_size = snapshot["tick_size"]
        buffer = self.breakout_buffer_ticks * tick_size
        extension_limit = self.max_extension_atr * atr

        long_breakout = price > upper + buffer and price > ema
        short_breakout = price < lower - buffer and price < ema

        if long_breakout and price - upper <= extension_limit:
            self._register_entry(sym)
            return self._open_signal(1, "donchian_upper_breakout", self._metrics(price, current_net, snapshot))

        if short_breakout and lower - price <= extension_limit:
            self._register_entry(sym)
            return self._open_signal(-1, "donchian_lower_breakdown", self._metrics(price, current_net, snapshot))

        return self._hold_signal("hold", price, current_net, snapshot)

    def _position_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        metrics = self._metrics(price, current_net, snapshot)
        state = self.entry_state.get(sym)
        direction = 1 if current_net > 0 else -1

        if state is None or not snapshot["ready"]:
            return {"signal": None, "reason": "holding", "metrics": metrics}

        atr = snapshot["atr"]
        midline = snapshot["midline"]
        holding_bars = self.bar_count[sym] - state.bar_index
        metrics["holding_bars"] = holding_bars

        if direction > 0:
            state.best_price = max(state.best_price, price)
            trailing_stop = state.best_price - self.atr_stop_mult * atr
            metrics["trailing_stop"] = trailing_stop
            if price <= trailing_stop:
                return self._exit_signal("atr_trailing_stop", metrics)
            if self.exit_on_midline and price < midline:
                return self._exit_signal("midline_exit", metrics)
        else:
            state.best_price = min(state.best_price, price)
            trailing_stop = state.best_price + self.atr_stop_mult * atr
            metrics["trailing_stop"] = trailing_stop
            if price >= trailing_stop:
                return self._exit_signal("atr_trailing_stop", metrics)
            if self.exit_on_midline and price > midline:
                return self._exit_signal("midline_exit", metrics)

        if holding_bars >= self.max_hold_bars:
            return self._exit_signal("time_exit", metrics)

        return {"signal": None, "reason": "holding", "metrics": metrics}

    def _snapshot(self, sym: str, bar: _Bar) -> dict:
        history = list(self.history[sym])
        tick_size = self._tick_size(sym)
        ema_value = self.ema[sym]
        ready = True
        reason = "ready"

        if len(history) < max(self.donchian_window, self.atr_period + 1, self.trend_window):
            ready = False
            reason = "warming_up"
            return {
                "ready": ready,
                "reason": reason,
                "history_len": len(history),
                "tick_size": tick_size,
                "ema": ema_value if ema_value is not None else bar.close,
            }

        channel_bars = history[-self.donchian_window:]
        upper = max(item.high for item in channel_bars)
        lower = min(item.low for item in channel_bars)
        midline = (upper + lower) / 2.0
        atr = self._atr(history)
        atr_ticks = atr / tick_size if tick_size > 0 else 0.0
        channel_width = upper - lower
        channel_atr = channel_width / atr if atr > 0 else 0.0

        if ema_value is None:
            ema_value = sum(item.close for item in history[-self.trend_window:]) / self.trend_window
        if atr <= 0:
            ready = False
            reason = "zero_atr"
        elif channel_atr < self.min_channel_atr:
            ready = False
            reason = "channel_too_narrow"

        return {
            "ready": ready,
            "reason": reason,
            "history_len": len(history),
            "upper": upper,
            "lower": lower,
            "midline": midline,
            "atr": atr,
            "atr_ticks": atr_ticks,
            "channel_width": channel_width,
            "channel_atr": channel_atr,
            "ema": ema_value,
            "tick_size": tick_size,
        }

    def _atr(self, history: list[_Bar]) -> float:
        sample = history[-(self.atr_period + 1):]
        true_ranges = []
        for prev, current in zip(sample, sample[1:]):
            true_ranges.append(max(
                current.high - current.low,
                abs(current.high - prev.close),
                abs(current.low - prev.close),
            ))
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    def _append_bar(self, sym: str, bar: _Bar):
        self.history[sym].append(bar)
        previous = self.ema[sym]
        if previous is None:
            closes = [item.close for item in self.history[sym]]
            if len(closes) >= self.trend_window:
                self.ema[sym] = sum(closes[-self.trend_window:]) / self.trend_window
            return

        alpha = 2.0 / (self.trend_window + 1.0)
        self.ema[sym] = alpha * bar.close + (1.0 - alpha) * previous

    def _entry_block_reason(self, sym: str) -> str | None:
        if self._has_pending_order(sym):
            return "pending_order"
        if self.allowed_entry_hours is not None and self.current_time.hour not in self.allowed_entry_hours:
            return "outside_entry_hours"
        last_exit_bar = self.last_exit_bar.get(sym)
        if last_exit_bar is not None and self.bar_count[sym] - last_exit_bar < self.cooldown_bars:
            return "cooldown"
        if self._entry_limit_reached(sym):
            return "daily_entry_limit"
        return None

    def _sync_entry_state(self, sym: str, current_net: int, price: float):
        state = self.entry_state.get(sym)
        if current_net == 0:
            if state is not None:
                self.last_exit_bar[sym] = self.bar_count[sym]
            self.entry_state[sym] = None
            return

        direction = 1 if current_net > 0 else -1
        if state is not None and state.direction == direction:
            return

        trade = self._last_trade_for_symbol(sym)
        entry_price = float(trade.price) if trade is not None else float(price)
        self.entry_state[sym] = _EntryState(direction, entry_price, self.bar_count[sym], entry_price)

    def _last_trade_for_symbol(self, sym: str):
        raw_code = pure_product_code(sym)
        for trade in reversed(self.broker.trade_history):
            if pure_product_code(trade.symbol) == raw_code:
                return trade
        return None

    def _has_pending_order(self, sym: str) -> bool:
        raw_code = pure_product_code(sym)
        return any(pure_product_code(order.symbol) == raw_code for order in self.broker.pending_orders)

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

    def _coerce_bar(self, bar: dict) -> _Bar | None:
        close = bar.get("close")
        if close is None or pd.isna(close):
            return None
        close = float(close)
        return _Bar(
            open=self._float_or_default(bar.get("open"), close),
            high=self._float_or_default(bar.get("high"), close),
            low=self._float_or_default(bar.get("low"), close),
            close=close,
            volume=self._float_or_default(bar.get("volume"), 0.0),
        )

    @staticmethod
    def _float_or_default(value, default: float) -> float:
        if value is None or pd.isna(value):
            return float(default)
        return float(value)

    @staticmethod
    def _parse_allowed_hours(value) -> set[int] | None:
        if value in (None, "", "all"):
            return None
        if isinstance(value, str):
            items = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
        else:
            items = list(value)
        return {int(item) for item in items if 0 <= int(item) <= 23}

    def _metrics(self, price: float, current_net: int, snapshot: dict) -> dict:
        return {"price": price, "current_net": current_net, **{f"indicator_{k}": v for k, v in snapshot.items()}}

    @staticmethod
    def _open_signal(direction: int, reason: str, metrics: dict) -> dict:
        return {"signal": int(direction), "position_mode": "target", "reason": reason, "metrics": metrics}

    @staticmethod
    def _hold_signal(reason: str, price: float, current_net: int, snapshot: dict) -> dict:
        return {
            "signal": None,
            "reason": reason,
            "metrics": {"price": price, "current_net": current_net, **{f"indicator_{k}": v for k, v in snapshot.items()}},
        }

    @staticmethod
    def _exit_signal(reason: str, metrics: dict) -> dict:
        return {"signal": 0, "position_mode": "flat", "reason": reason, "metrics": metrics}

    def _tick_size(self, sym: str) -> float:
        raw_code = pure_product_code(sym)
        meta = self.account.fee_model._get_meta_data(raw_code)
        return float(meta["tick_size"])
