# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
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
class _SessionState:
    session_key: object | None = None
    bars_in_session: int = 0
    opening_high: float | None = None
    opening_low: float | None = None
    opening_open: float | None = None


@dataclass
class _EntryState:
    direction: int
    price: float
    bar_index: int
    best_price: float
    session_key: object | None


class OpeningRangeACDStrategy(GeneralSignalStrategy):
    """
    Opening-range breakout strategy inspired by ACD/ORB.

    The opening range is built from the first N bars of each trading session.
    Entries are evaluated only after that range is complete. Indicator values
    use prior completed bars plus the current close, avoiding look-ahead.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        opening_range_bars: int = 6,
        atr_period: int = 48,
        trend_window: int = 144,
        breakout_buffer_ticks: float = 1.0,
        min_range_atr: float = 0.8,
        max_extension_atr: float = 1.2,
        atr_stop_mult: float = 2.4,
        trail_atr_mult: float = 3.2,
        take_profit_atr: float = 0.0,
        exit_on_range_reentry: bool = True,
        max_hold_bars: int = 96,
        cooldown_bars: int = 12,
        max_entries_per_symbol_per_day: int | None = 2,
        session_start_hours: Iterable[int] | str | None = "9,21",
        allowed_entry_hours: Iterable[int] | str | None = "9,10,21,22,23,0,1",
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.opening_range_bars = int(opening_range_bars)
        self.atr_period = int(atr_period)
        self.trend_window = int(trend_window)
        self.breakout_buffer_ticks = float(breakout_buffer_ticks)
        self.min_range_atr = float(min_range_atr)
        self.max_extension_atr = float(max_extension_atr)
        self.atr_stop_mult = float(atr_stop_mult)
        self.trail_atr_mult = float(trail_atr_mult)
        self.take_profit_atr = float(take_profit_atr)
        self.exit_on_range_reentry = bool(exit_on_range_reentry)
        self.max_hold_bars = int(max_hold_bars)
        self.cooldown_bars = int(cooldown_bars)
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day in (None, "", 0) else int(max_entries_per_symbol_per_day)
        )
        self.session_start_hours = self._parse_allowed_hours(session_start_hours) or {9, 21}
        self.allowed_entry_hours = self._parse_allowed_hours(allowed_entry_hours)

        if self.opening_range_bars < 1:
            raise ValueError("opening_range_bars must be positive")
        if self.atr_period < 2:
            raise ValueError("atr_period must be at least 2")
        if self.trend_window < 2:
            raise ValueError("trend_window must be at least 2")
        if self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars must be positive")

        history_size = max(self.atr_period + 2, self.trend_window + 2, 256)
        self.history: dict[str, Deque[_Bar]] = {sym: deque(maxlen=history_size) for sym in self.symbols}
        self.session_state: dict[str, _SessionState] = {sym: _SessionState() for sym in self.symbols}
        self.ema: dict[str, float | None] = {sym: None for sym in self.symbols}
        self.bar_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.entry_state: dict[str, _EntryState | None] = {sym: None for sym in self.symbols}
        self.last_exit_bar: dict[str, int | None] = {sym: None for sym in self.symbols}
        self.entry_count_date: dict[str, object | None] = {sym: None for sym in self.symbols}
        self.entry_count: dict[str, int] = {sym: 0 for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy OpeningRangeACD] symbols={self.symbols} | "
            f"or_bars={self.opening_range_bars} | atr={self.atr_period} | trend={self.trend_window}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        for sym in self.symbols:
            raw_bar = bar_data.get(sym)
            if not raw_bar or not bool(raw_bar.get("is_fresh", True)):
                continue

            bar = self._coerce_bar(raw_bar)
            if bar is None:
                continue

            self.bar_count[sym] += 1
            session = self._update_session(sym, bar)
            snapshot = self._snapshot(sym, bar, session)

            price = bar.close
            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, price, session.session_key)

            if current_net != 0:
                signals[sym] = self._position_signal(sym, price, current_net, snapshot)
            else:
                signals[sym] = self._flat_signal(sym, price, current_net, snapshot)

            self._append_bar(sym, bar)

        return signals

    def _flat_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        blocked = self._entry_block_reason(sym)
        if blocked:
            return self._hold_signal(blocked, price, current_net, snapshot)
        if not snapshot["ready"]:
            return self._hold_signal(snapshot["reason"], price, current_net, snapshot)

        upper = snapshot["opening_high"]
        lower = snapshot["opening_low"]
        atr = snapshot["atr"]
        ema = snapshot["ema"]
        tick_size = snapshot["tick_size"]
        buffer = self.breakout_buffer_ticks * tick_size
        extension_limit = self.max_extension_atr * atr

        long_breakout = price > upper + buffer and price > ema
        short_breakout = price < lower - buffer and price < ema

        if long_breakout and price - upper <= extension_limit:
            self._register_entry(sym)
            return self._open_signal(1, "opening_range_a_up", self._metrics(price, current_net, snapshot))

        if short_breakout and lower - price <= extension_limit:
            self._register_entry(sym)
            return self._open_signal(-1, "opening_range_a_down", self._metrics(price, current_net, snapshot))

        return self._hold_signal("hold", price, current_net, snapshot)

    def _position_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        metrics = self._metrics(price, current_net, snapshot)
        state = self.entry_state.get(sym)
        direction = 1 if current_net > 0 else -1

        if state is None or not snapshot["has_range"]:
            return {"signal": None, "reason": "holding", "metrics": metrics}

        atr = snapshot.get("atr", 0.0)
        upper = snapshot["opening_high"]
        lower = snapshot["opening_low"]
        holding_bars = self.bar_count[sym] - state.bar_index
        metrics["holding_bars"] = holding_bars

        if direction > 0:
            state.best_price = max(state.best_price, price)
            hard_stop = state.price - self.atr_stop_mult * atr
            trail_stop = state.best_price - self.trail_atr_mult * atr
            range_stop = lower
            stop_price = max(hard_stop, trail_stop, range_stop)
            metrics["stop_price"] = stop_price
            if price <= stop_price:
                return self._exit_signal("acd_stop", metrics)
            if self.exit_on_range_reentry and price < upper:
                return self._exit_signal("range_reentry_exit", metrics)
            if self.take_profit_atr > 0 and price - state.price >= self.take_profit_atr * atr:
                return self._exit_signal("take_profit", metrics)
        else:
            state.best_price = min(state.best_price, price)
            hard_stop = state.price + self.atr_stop_mult * atr
            trail_stop = state.best_price + self.trail_atr_mult * atr
            range_stop = upper
            stop_price = min(hard_stop, trail_stop, range_stop)
            metrics["stop_price"] = stop_price
            if price >= stop_price:
                return self._exit_signal("acd_stop", metrics)
            if self.exit_on_range_reentry and price > lower:
                return self._exit_signal("range_reentry_exit", metrics)
            if self.take_profit_atr > 0 and state.price - price >= self.take_profit_atr * atr:
                return self._exit_signal("take_profit", metrics)

        if holding_bars >= self.max_hold_bars:
            return self._exit_signal("time_exit", metrics)

        return {"signal": None, "reason": "holding", "metrics": metrics}

    def _update_session(self, sym: str, bar: _Bar) -> _SessionState:
        state = self.session_state[sym]
        session_key = self._session_key(self.current_time)
        if state.session_key != session_key:
            state.session_key = session_key
            state.bars_in_session = 0
            state.opening_high = None
            state.opening_low = None
            state.opening_open = None

        state.bars_in_session += 1
        if state.bars_in_session <= self.opening_range_bars:
            if state.opening_open is None:
                state.opening_open = bar.open
            state.opening_high = bar.high if state.opening_high is None else max(state.opening_high, bar.high)
            state.opening_low = bar.low if state.opening_low is None else min(state.opening_low, bar.low)
        return state

    def _snapshot(self, sym: str, bar: _Bar, session: _SessionState) -> dict:
        history = list(self.history[sym])
        tick_size = self._tick_size(sym)
        ema_value = self.ema[sym]
        has_range = session.opening_high is not None and session.opening_low is not None

        base = {
            "session_key": str(session.session_key),
            "bars_in_session": session.bars_in_session,
            "opening_high": session.opening_high,
            "opening_low": session.opening_low,
            "opening_open": session.opening_open,
            "has_range": has_range,
            "tick_size": tick_size,
            "ema": ema_value if ema_value is not None else bar.close,
        }

        if session.bars_in_session <= self.opening_range_bars:
            return {**base, "ready": False, "reason": "opening_range_building"}

        if not has_range:
            return {**base, "ready": False, "reason": "missing_opening_range"}

        if len(history) < max(self.atr_period + 1, self.trend_window):
            return {**base, "ready": False, "reason": "warming_up", "history_len": len(history)}

        atr = self._atr(history)
        if ema_value is None:
            ema_value = sum(item.close for item in history[-self.trend_window:]) / self.trend_window
        opening_range = session.opening_high - session.opening_low
        range_atr = opening_range / atr if atr > 0 else 0.0

        ready = True
        reason = "ready"
        if atr <= 0:
            ready = False
            reason = "zero_atr"
        elif range_atr < self.min_range_atr:
            ready = False
            reason = "opening_range_too_narrow"

        return {
            **base,
            "ready": ready,
            "reason": reason,
            "history_len": len(history),
            "atr": atr,
            "atr_ticks": atr / tick_size if tick_size > 0 else 0.0,
            "ema": ema_value,
            "opening_range": opening_range,
            "range_atr": range_atr,
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

    def _sync_entry_state(self, sym: str, current_net: int, price: float, session_key):
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
        self.entry_state[sym] = _EntryState(direction, entry_price, self.bar_count[sym], entry_price, session_key)

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
        current_date = self._trading_date(self.current_time)
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        return self.entry_count[sym] >= self.max_entries_per_symbol_per_day

    def _register_entry(self, sym: str):
        if self.max_entries_per_symbol_per_day is None:
            return
        current_date = self._trading_date(self.current_time)
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        self.entry_count[sym] += 1

    def _session_key(self, timestamp: datetime):
        if timestamp.hour >= 21 or timestamp.hour < 3:
            base_date = timestamp.date() if timestamp.hour >= 21 else (timestamp - timedelta(days=1)).date()
            return (base_date, "night")
        if 9 <= timestamp.hour < 15:
            return (timestamp.date(), "day")
        return (timestamp.date(), f"h{timestamp.hour}")

    def _trading_date(self, timestamp: datetime):
        if timestamp.hour >= 21:
            return (timestamp + timedelta(days=1)).date()
        return timestamp.date()

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
